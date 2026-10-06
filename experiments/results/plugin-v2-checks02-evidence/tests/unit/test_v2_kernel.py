import sqlite3
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from supergoal_runtime.v2 import Kernel, StaleLease, verify


@pytest.fixture
def setup_kernel(tmp_path):
    now = [100.0]
    k = Kernel(tmp_path / "state.db", clock=lambda: now[0])
    k.create("g", {"outcome": "Deliver the configured result"})
    return k, now


def test_only_one_owner_and_no_reentrant_dispatch(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "a")
    assert lease
    assert k.claim("g", "b") is None
    assert k.claim("g", "a") is None
    with pytest.raises(StaleLease):
        k.finish(replace(lease, owner="b"), {"verdict": "pass"})


def test_simultaneous_connections_cannot_dispatch_twice(setup_kernel):
    k, _ = setup_kernel
    other = Kernel(k.path, clock=lambda: 100)
    barrier = threading.Barrier(2)

    def claim(kernel, owner):
        barrier.wait()
        return kernel.claim('g', owner)

    with ThreadPoolExecutor(2) as pool:
        a = pool.submit(claim, k, 'first')
        b = pool.submit(claim, other, 'second')
        assert sum(x is not None for x in (a.result(), b.result())) == 1


def test_duplicate_receipt_does_not_consume_budget_twice(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "a")
    result = k.finish(lease, {"verdict": "fail", "reason": "missing file"})
    assert k.finish(lease, {"verdict": "fail", "reason": "missing file"}) == result
    assert k.state("g")["turns"] == 1
    with pytest.raises(ValueError, match="conflicting"):
        k.finish(lease, {"verdict": "pass"})


def test_expired_owner_cannot_commit_or_blindly_reexecute(setup_kernel):
    k, now = setup_kernel
    lease = k.claim("g", "a", ttl=10)
    now[0] += 11
    assert k.claim("g", "b") is None
    with pytest.raises(StaleLease):
        k.finish(lease, {"verdict": "pass"})
    assert k.state("g")["status"] == "needs_reconciliation"


def stop_evidence():
    return {"execution_stopped": True, "external_effects": "sandbox_local_only",
            "snapshot_id": "sha256:preserved-workspace", "observed_by": "test-host"}


def test_interrupted_attempt_requires_host_evidence_and_fences_old_owner(setup_kernel):
    k, now = setup_kernel
    lease = k.claim("g", "old", ttl=5)
    now[0] += 6
    k.interrupt(lease, reason="worker lost")
    with pytest.raises(ValueError):
        k.recover_execution(lease, evidence={"verdict": "pass"})
    receipt = k.recover_execution(lease, evidence=stop_evidence())
    assert receipt == {"status": "active", "turns": 1}
    assert k.recover_execution(lease, evidence=stop_evidence()) == receipt
    assert k.state("g")["turns"] == 1
    with pytest.raises(StaleLease):
        k.finish(lease, {"verdict": "pass"})
    new = k.claim("g", "new")
    assert new.item == 2
    assert k.finish(new, {"verdict": "pass"})["status"] == "succeeded"


def test_recovery_after_new_process_and_expiry_quarantine(setup_kernel):
    k, now = setup_kernel
    lease = k.claim("g", "old", ttl=1)
    now[0] += 2
    assert k.claim("g", "new") is None
    other = Kernel(k.path, clock=lambda: now[0])
    other.interrupt(lease, reason="recorded process exit")
    assert other.recover_execution(lease, evidence=stop_evidence())["turns"] == 1
    with pytest.raises(ValueError, match="conflicting"):
        other.recover_execution(lease, evidence={**stop_evidence(), "snapshot_id": "different"})


def test_recovery_cannot_bypass_cancel_or_turn_budget(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "old")
    k.interrupt(lease, reason="stopped")
    k.cancel("g")
    with pytest.raises(StaleLease):
        k.recover_execution(lease, evidence=stop_evidence())
    k.create("one", {"outcome": "one attempt only"}, max_turns=1)
    only = k.claim("one", "old")
    k.interrupt(only, reason="stopped")
    assert k.recover_execution(only, evidence=stop_evidence()) == {"status": "budget_exhausted", "turns": 1}
    assert k.claim("one", "next") is None


def test_recovery_rejects_unresolved_external_effects(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "old")
    k.interrupt(lease, reason="uncertain external call")
    with pytest.raises(ValueError):
        k.recover_execution(lease, evidence={**stop_evidence(), "external_effects": "unknown"})
    assert k.state("g")["status"] == "needs_reconciliation"


def test_cancel_fences_inflight_outcome_and_wait_event(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "a")
    k.cancel("g")
    with pytest.raises(StaleLease):
        k.finish(lease, {"verdict": "pass"})
    assert not k.wake("g", event_id="late-input", key="ready")
    assert k.claim("g", "b") is None


def test_wait_survives_new_process_and_wakeup_is_exactly_once(setup_kernel):
    k, now = setup_kernel
    lease = k.claim("g", "a")
    k.finish(lease, {"verdict": "fail"}, wait_key="input", wake_at=200)
    assert k.claim("g", "b") is None
    assert not k.wake("g", event_id="early", key="wrong")
    code = "from supergoal_runtime.v2 import Kernel; import sys; k=Kernel(sys.argv[1],clock=lambda:150); assert k.wake('g',event_id='arrival',key='input'); assert not k.wake('g',event_id='arrival',key='input')"
    subprocess.run([sys.executable, "-c", code, k.path], check=True)
    assert k.claim("g", "new-process").item == 2


def test_timer_wakes_without_model_polling(setup_kernel):
    k, now = setup_kernel
    k.finish(k.claim("g", "a"), {"verdict": "fail"}, wake_at=101)
    assert not k.wake("g", event_id="tick0")
    now[0] = 101
    assert k.wake("g", event_id="tick1")


def test_transaction_rolls_back_receipt_and_next_work_on_crash(setup_kernel):
    k, _ = setup_kernel
    lease = k.claim("g", "a")
    with sqlite3.connect(k.path) as db:
        db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts BEGIN SELECT RAISE(ABORT, 'crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        k.finish(lease, {"verdict": "fail"})
    assert k.state("g")["turns"] == 0
    with sqlite3.connect(k.path) as db:
        assert db.execute("SELECT count(*) FROM work").fetchone()[0] == 1
        db.execute("DROP TRIGGER fail_receipt")
    assert k.finish(lease, {"verdict": "fail"})["turns"] == 1
    assert k.claim("g", "b").item == 2


@pytest.mark.parametrize("verdict", [True, "done", "true", None])
def test_invalid_verifier_result_cannot_finish(setup_kernel, verdict):
    k, _ = setup_kernel
    with pytest.raises(ValueError):
        k.finish(k.claim("g", "a"), {"verdict": verdict})
    assert k.state("g")["status"] == "active"


def test_unknown_verification_stops_without_false_success(setup_kernel):
    k, _ = setup_kernel
    k.finish(k.claim("g", "a"), {"verdict": "unknown"})
    assert k.state("g")["status"] == "verification_unavailable"
    assert k.claim("g", "a") is None


def test_real_exit_code_and_artifact_hash_control_acceptance(tmp_path):
    artifact = tmp_path / "output.txt"
    artifact.write_text("actual result")
    contract = {"artifacts": ["output.txt"], "checks": [{"id": "test", "argv": [sys.executable, "-c", "print('all passed'); raise SystemExit(1)"]}]}
    assert verify(tmp_path, contract)["verdict"] == "fail"
    contract["checks"][0]["argv"] = [sys.executable, "-c", "pass"]
    first = verify(tmp_path, contract)
    assert first["verdict"] == "pass"
    artifact.write_text("changed result")
    assert verify(tmp_path, contract)["artifacts"] != first["artifacts"]
    contract["checks"][0]["argv"] = [sys.executable, "-c", "from pathlib import Path;Path('output.txt').write_text('mutated in check')"]
    assert verify(tmp_path, contract)["verdict"] == "fail"


def test_missing_contract_or_escaping_file_cannot_pass(tmp_path):
    assert verify(tmp_path, {})["verdict"] == "unknown"
    assert verify(tmp_path, {"artifacts": ["../secret"], "checks": [{}]})["verdict"] == "unknown"
