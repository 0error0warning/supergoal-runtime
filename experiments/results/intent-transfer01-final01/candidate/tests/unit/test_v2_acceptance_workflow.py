import json
import sys

import pytest

from supergoal_runtime.v2.acceptance import calibrate, load_policy, verify_policy
from supergoal_runtime.v2.workflow import Workflow


@pytest.fixture
def acceptance_case(tmp_path):
    workspace = tmp_path / "task"
    workspace.mkdir()
    for folder, value in [("positive", 42), ("negative", 0)]:
        fixture = tmp_path / folder
        fixture.mkdir()
        (fixture / "answer.txt").write_text(str(value))
    checker = tmp_path / "check.py"
    checker.write_text("from pathlib import Path\nraise SystemExit(0 if Path('answer.txt').read_text()=='42' else 1)\n")
    data = {"schema": 1, "outcome": "Deliver value 42", "scope": "Exact answer.txt contents only",
            "workspace": "task", "criteria": {"value": "answer.txt contains 42"},
            "artifacts": ["answer.txt"], "max_turns": 4,
            "checks": [{"id": "value", "covers": ["value"], "timeout": 2,
                        "argv": [sys.executable, "{policy_dir}/check.py"], "source_files": ["check.py"]}],
            "calibration": [{"path": "positive", "expect": "pass"}, {"path": "negative", "expect": "fail"}]}
    config = tmp_path / "policy.json"
    config.write_text(json.dumps(data))
    return config, workspace, checker, data


def test_calibration_rejects_wrong_anchor_and_always_pass_checker(acceptance_case):
    path, workspace, checker, _ = acceptance_case
    original = checker.read_text()
    checker.write_text(original.replace("=='42'", "=='0'"))
    result = calibrate(load_policy(path))
    assert result["valid"] is False
    assert result["cases"][0]["observed"]["verdict"] == "fail"
    checker.write_text("raise SystemExit(0)\n")
    assert calibrate(load_policy(path))["valid"] is False
    checker.write_text(original)
    policy = load_policy(path)
    receipt = calibrate(policy)
    assert receipt["valid"] is True
    (workspace / "answer.txt").write_text("42")
    assert verify_policy(workspace, policy, receipt)["verdict"] == "pass"


def test_opt_in_work_guidance_survives_restart_and_operator_amendment(acceptance_case, tmp_path):
    path, _, _, data = acceptance_case
    data.update(work_policy="evidence-v1", verification_unknown="continue")
    path.write_text(json.dumps(data))
    policy = load_policy(path)
    store = Workflow(tmp_path / "work-policy.sqlite3")
    goal = store.start("session", policy, calibrate(policy))
    restored = Workflow(store.path)
    assert restored.state(goal)["contract"]["work_policy"] == "evidence-v1"
    envelope = restored.envelope("session")
    assert "Research should resolve a named decision" in envelope["prompt"]
    assert restored.claim_envelope("session", envelope["token"], "host", envelope["state_version"])
    lease = restored.running("session", "host")
    assert restored.finish(lease, {"verdict": "unknown", "reason": "Need a local check", "retryable": True})["status"] == "active"
    restored.hold(goal, reason="operator revises continuation policy")
    data.pop("work_policy")
    data.pop("verification_unknown")
    path.write_text(json.dumps(data))
    amended = load_policy(path)
    restored.amend(goal, amended, calibrate(amended))
    assert restored.state(goal)["contract"]["work_policy"] is None
    assert restored.state(goal)["contract"]["verification_unknown"] == "stop"
    assert restored.state(goal)["turns"] == 1


def test_policy_and_code_drift_cannot_reuse_calibration(acceptance_case):
    path, workspace, checker, _ = acceptance_case
    policy = load_policy(path)
    receipt = calibrate(policy)
    (workspace / "answer.txt").write_text("42")
    checker.write_text("raise SystemExit(0)\n")
    assert verify_policy(workspace, policy, receipt)["verdict"] == "unknown"
    policy["criteria"]["value"] = "anything"
    assert "fingerprint" in verify_policy(workspace, policy, receipt)["reason"]


def test_uncovered_criteria_or_missing_negative_are_refused(acceptance_case):
    path, _, _, data = acceptance_case
    data["criteria"]["extra"] = "Not covered by the checker"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="uncovered"):
        load_policy(path)
    del data["criteria"]["extra"]
    data["calibration"] = data["calibration"][:1]
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="known-incorrect"):
        load_policy(path)


def test_independent_checks_disagree_on_actual_result(acceptance_case):
    path, workspace, _, data = acceptance_case
    second = path.parent / "positive_check.py"
    second.write_text("from pathlib import Path\nraise SystemExit(0 if int(Path('answer.txt').read_text())>0 else 1)\n")
    data["checks"].append({"id": "positive", "covers": ["value"], "timeout": 2,
                           "argv": [sys.executable, str(second)], "source_files": [str(second)]})
    path.write_text(json.dumps(data))
    policy = load_policy(path)
    receipt = calibrate(policy)
    assert receipt["valid"]
    (workspace / "answer.txt").write_text("43")
    result = verify_policy(workspace, policy, receipt)
    assert result["verdict"] == "disputed"
    assert result["conflicting_criteria"] == ["value"]


@pytest.fixture
def workflow(acceptance_case, tmp_path):
    policy = load_policy(acceptance_case[0])
    now = [100.0]
    store = Workflow(tmp_path / "state.sqlite3", clock=lambda: now[0])
    goal = store.start("session", policy, calibrate(policy))
    return store, goal, now


def test_restart_recovers_unclaimed_work_but_never_reclaims_inflight(workflow):
    store, goal, now = workflow
    envelope = store.envelope("session")
    fresh = Workflow(store.path, clock=lambda: now[0])
    assert fresh.recover("boot-2") == [envelope]
    assert fresh.claim_envelope("session", envelope["token"], "boot-2", envelope["state_version"], ttl=10)
    assert not store.claim_envelope("session", envelope["token"], "boot-1", envelope["state_version"])
    assert store.recover("boot-3") == []
    now[0] += 11
    assert store.recover("boot-3") == []
    assert store.state(goal)["status"] == "needs_reconciliation"


def test_paused_inflight_requires_explicit_effect_review(workflow):
    store, goal, _ = workflow
    envelope = store.envelope("session")
    store.claim_envelope("session", envelope["token"], "worker", 0)
    old = store.running("session", "worker")
    store.hold(goal, reason="pause")
    with pytest.raises(ValueError, match="side effects"):
        store.resume(goal)
    from supergoal_runtime.v2 import StaleLease
    with pytest.raises(StaleLease):
        store.finish(old, {"verdict": "pass"})
    store.reconcile(goal, note="old worker stopped; no unreviewed external actions")
    store.resume(goal)
    assert store.envelope("session")["token"] != envelope["token"]


def test_dispute_does_not_grant_completion_or_reset_budget(workflow, acceptance_case):
    store, goal, _ = workflow
    envelope = store.envelope("session")
    store.claim_envelope("session", envelope["token"], "worker", 0)
    result = store.finish(store.running("session", "worker"), {"verdict": "disputed", "reason": "conflicting evidence"})
    assert result["status"] == "contract_disputed"
    assert store.recover("next") == []
    with pytest.raises(ValueError, match="review"):
        store.resume(goal)
    policy = load_policy(acceptance_case[0])
    receipt = calibrate(policy)
    store.amend(goal, policy, receipt)
    store.resume(goal)
    assert store.state(goal)["turns"] == 1
    assert store.state(goal)["max_turns"] == 4
    with store._tx() as db:
        amendment = json.loads(db.execute("SELECT body FROM events WHERE kind='policy_amended'").fetchone()[0])
    assert amendment["previous"]["acceptance"]["fingerprint"] == policy["fingerprint"]
    assert amendment["actor"] == "operator"


def test_wait_and_compression_preserve_identity_without_duplicate_dispatch(workflow):
    store, goal, _ = workflow
    store.wait_for(goal, "new-input")
    assert not store.signal(goal, "wrong-input")
    store.rotate("session", "compressed")
    assert store.bound("session") is None
    assert store.signal(goal, "new-input")
    assert not store.signal(goal, "new-input")
    envelope = store.envelope("compressed")
    assert store.claim_envelope("compressed", envelope["token"], "worker", envelope["state_version"])
    store.finish(store.running("compressed", "worker"), {"verdict": "pass"})
    assert store.recover("restart") == []


def test_clear_retains_history_and_fences_old_envelope(workflow):
    store, goal, _ = workflow
    old = store.envelope("session")
    store.clear("session")
    assert not store.claim_envelope("session", old["token"], "worker", old["state_version"])
    assert store.state(goal)["status"] == "cancelled"
    with store._tx() as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] >= 2
