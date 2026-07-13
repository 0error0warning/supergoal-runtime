from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest

from supergoal_runtime.command import SupergoalCommandHandler
from supergoal_runtime.plugin import (
    CONTROLLER_STORE_TIMEOUT_SECONDS,
    LLM_EVALUATION_TIMEOUT_SECONDS,
    _PluginRuntime,
    _llm_text,
)
from supergoal_runtime.policy import ToolHookHandler
from supergoal_runtime.prompts import CONTINUATION_PROMPT_PREFIX
from supergoal_runtime.runtime import EVALUATION_BUDGET_SECONDS, RuntimeManager
from supergoal_runtime.store import SupergoalStore


def _outbox_status(store: SupergoalStore, token: str) -> str:
    with sqlite3.connect(store.db_path) as conn:
        row = conn.execute(
            "SELECT status FROM continuation_outbox WHERE token=?",
            (token,),
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_done_run_rejects_tool_evidence_even_with_stale_active_snapshot(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    state = manager.start("sess", "produce artifact")
    stale_active_state = manager.load_state_for_session("sess")
    assert stale_active_state is not None and stale_active_state.status == "active"

    state.status = "done"
    manager.save_state(state)
    hook = ToolHookHandler(store)
    # Reproduce the original race: Python loaded an active state before a
    # terminal lifecycle write won. The transaction-level guard must decide.
    hook._load_state = lambda _session_id: (state.goal_run_id, stale_active_state)  # type: ignore[method-assign]
    hook.post_tool_call(
        session_id="sess",
        tool_name="write_file",
        args={"path": "/tmp/final.txt"},
        result={"success": True},
        tool_call_id="late-tool-call",
        turn_id="late-turn",
        status="ok",
    )

    evidence = [
        event
        for event in store.load_events(state.goal_run_id)
        if event["type"] == "tool_evidence_observed"
    ]
    assert evidence == []


def test_pause_wins_evaluator_cas_and_late_workers_cannot_write(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    judge_started = threading.Event()
    critic_started = threading.Event()
    release = threading.Event()
    judge_finished = threading.Event()
    critic_finished = threading.Event()

    def judge(*_args: Any, **_kwargs: Any) -> tuple[str, str, bool]:
        judge_started.set()
        release.wait(timeout=2)
        judge_finished.set()
        return "continue", "late judge result", False

    def critic(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        critic_started.set()
        release.wait(timeout=2)
        critic_finished.set()
        return {"next_best_action": "late critic result"}

    manager = RuntimeManager(
        store=store,
        judge=judge,
        critic=critic,
        evaluation_budget_seconds=0.05,
    )
    state = manager.start("sess", "race-safe mission")

    with ThreadPoolExecutor(max_workers=1) as pool:
        started_at = time.monotonic()
        result_future = pool.submit(
            manager.after_turn,
            "sess",
            final_response="work is still in progress",
            user_message=f"{CONTINUATION_PROMPT_PREFIX}\nGoal: race-safe mission",
            turn_id="turn-race",
        )
        assert judge_started.wait(timeout=1)
        assert critic_started.wait(timeout=1)
        manager.pause("sess")
        assert result_future.result(timeout=1) is None
        assert time.monotonic() - started_at < 1.0

    before_late_return = store.load_events(state.goal_run_id)
    release.set()
    assert judge_finished.wait(timeout=1)
    assert critic_finished.wait(timeout=1)
    time.sleep(0.02)

    persisted = manager.load_state_for_session("sess")
    assert persisted is not None
    assert persisted.status == "paused"
    assert persisted.turns_used == 0
    assert store.load_events(state.goal_run_id) == before_late_return
    assert [event["type"] for event in before_late_return] == ["started", "paused"]


def test_llm_complete_gets_18_second_timeout_and_legacy_fake_is_supported():
    class ProductionStyleLLM:
        def __init__(self) -> None:
            self.timeout: float | None = None

        def complete(self, *, messages, timeout):
            self.timeout = timeout
            return {"content": '{"done": false}'}

    class LegacyFakeLLM:
        def complete(self, *, messages):
            return {"content": '{"done": false}'}

    production = ProductionStyleLLM()
    assert _llm_text(production, [{"role": "user", "content": "check"}])
    assert production.timeout == LLM_EVALUATION_TIMEOUT_SECONDS == 18.0
    assert _llm_text(LegacyFakeLLM(), [{"role": "user", "content": "check"}])
    assert EVALUATION_BUDGET_SECONDS == 20.0
    assert RuntimeManager(evaluation_budget_seconds=999).evaluation_budget_seconds == 20.0
    runtime = _PluginRuntime(SimpleNamespace(llm=None))
    assert runtime.store.timeout == CONTROLLER_STORE_TIMEOUT_SECONDS == 2.0


def test_manual_new_and_auto_reset_pause_old_run_and_cancel_outbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runtime = _PluginRuntime(SimpleNamespace(llm=None))

    manual_state, manual_envelope = runtime.manager.start_for_command(
        "manual-old", "manual reset mission"
    )
    runtime.on_session_finalize(
        session_id="manual-old",
        old_session_id="manual-old",
        new_session_id="manual-new",
        reason="new_session",
    )
    runtime.on_session_reset(
        session_id="manual-new",
        old_session_id="manual-old",
        new_session_id="manual-new",
        reason="new_session",
    )

    manual_persisted = runtime.manager.load_state_for_session("manual-old")
    assert manual_persisted is not None and manual_persisted.status == "paused"
    assert runtime.manager.load_state_for_session("manual-new") is None
    assert _outbox_status(runtime.store, manual_envelope["token"]) == "cancelled"
    assert [
        event["type"] for event in runtime.store.load_events(manual_state.goal_run_id)
    ].count("session_finalized") == 1

    auto_state, auto_envelope = runtime.manager.start_for_command(
        "auto-old", "auto reset mission"
    )
    runtime.on_session_reset(
        session_id="auto-new",
        old_session_id="auto-old",
        new_session_id="auto-new",
        reason="idle_reset",
    )

    auto_persisted = runtime.manager.load_state_for_session("auto-old")
    assert auto_persisted is not None and auto_persisted.status == "paused"
    assert runtime.manager.load_state_for_session("auto-new") is None
    assert _outbox_status(runtime.store, auto_envelope["token"]) == "cancelled"
    assert [
        event["type"] for event in runtime.store.load_events(auto_state.goal_run_id)
    ].count("session_finalized") == 1


@pytest.mark.parametrize("verb", ["pause", "clear"])
def test_pause_and_clear_cancel_already_enqueued_continuation(tmp_path, verb):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    _state, envelope = manager.start_for_command("sess", "cancel queued work")

    getattr(manager, verb)("sess")

    assert _outbox_status(store, envelope["token"]) == "cancelled"
    assert not store.claim_continuation(
        session_id="sess",
        token=envelope["token"],
        state_version=envelope["state_version"],
        claim_owner="same-process",
    )
    assert store.recover_continuations(claim_owner="next-process") == []


def test_restart_recovers_old_owner_claim_exactly_once(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    _state, envelope = manager.start_for_command("sess", "restart-safe mission")

    assert store.claim_continuation(
        session_id="sess",
        token=envelope["token"],
        state_version=envelope["state_version"],
        claim_owner="old-boot",
    )
    assert _outbox_status(store, envelope["token"]) == "claimed"

    recovered = store.recover_continuations(claim_owner="new-boot")
    assert recovered == [
        {
            "session_id": envelope["session_id"],
            "token": envelope["token"],
            "prompt": envelope["prompt"],
            "state_version": envelope["state_version"],
        }
    ]
    assert store.claim_continuation(
        session_id="sess",
        token=envelope["token"],
        state_version=envelope["state_version"],
        claim_owner="new-boot",
    )
    assert store.recover_continuations(claim_owner="new-boot") == []
    assert not store.claim_continuation(
        session_id="sess",
        token=envelope["token"],
        state_version=envelope["state_version"],
        claim_owner="new-boot",
    )


def test_gateway_shutdown_keeps_active_run_recoverable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    runtime = _PluginRuntime(SimpleNamespace(llm=None))
    state, envelope = runtime.manager.start_for_command(
        "sess", "survive gateway restart"
    )

    runtime.on_session_finalize(session_id="sess", reason="shutdown")

    persisted = runtime.manager.load_state_for_session("sess")
    assert persisted is not None and persisted.status == "active"
    assert _outbox_status(runtime.store, envelope["token"]) == "pending"
    assert runtime.store.recover_continuations(claim_owner="next-boot") == [
        {
            "session_id": "sess",
            "token": envelope["token"],
            "prompt": envelope["prompt"],
            "state_version": envelope["state_version"],
        }
    ]
    assert [event["type"] for event in runtime.store.load_events(state.goal_run_id)] == [
        "started"
    ]


@pytest.mark.asyncio
async def test_command_enqueue_carries_trusted_continuation_metadata(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    handler = SupergoalCommandHandler(manager)
    captured: dict[str, Any] = {}

    async def enqueue_followup(prompt: str, **kwargs: Any) -> bool:
        captured["prompt"] = prompt
        captured.update(kwargs)
        return True

    ctx = SimpleNamespace(session_id="sess", enqueue_followup=enqueue_followup)
    result = await handler(ctx, "start metadata mission")

    assert result.startswith("Started /sgx supergoal")
    assert captured["plugin_id"] == "supergoal-runtime"
    assert captured["controller_name"] == "supergoal-runtime"
    assert str(captured["continuation_token"]).startswith("sgx_")
    assert isinstance(captured["state_version"], int)
    assert _outbox_status(store, captured["continuation_token"]) == "pending"
