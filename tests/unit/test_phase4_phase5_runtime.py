from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from supergoal_runtime.command import SupergoalCommandHandler
from supergoal_runtime.policy import PolicyGuard, PermissionContract, ToolHookHandler
from supergoal_runtime.runtime import RuntimeManager
from supergoal_runtime.store import SupergoalStore


class CommandCtx(SimpleNamespace):
    def __init__(self, session_id: str):
        super().__init__(session_id=session_id)
        self.enqueued: list[str] = []

    async def enqueue_followup(self, prompt: str) -> bool:
        self.enqueued.append(prompt)
        return True


@pytest.mark.asyncio
async def test_command_requires_explicit_start_text_and_enqueues(tmp_path):
    manager = RuntimeManager(store=SupergoalStore(db_path=tmp_path / "state.db"))
    handler = SupergoalCommandHandler(manager)
    ctx = CommandCtx("sess")

    assert await handler(ctx, "plain mission text") == "Usage: /sgx start <mission>. Plain /sgx <text> does not start a supergoal."
    assert manager.load_state_for_session("sess") is None

    result = await handler(ctx, "start build verified artifact")

    assert result.startswith("Started /sgx supergoal")
    assert len(ctx.enqueued) == 1
    assert manager.load_state_for_session("sess").goal == "build verified artifact"


@pytest.mark.asyncio
async def test_pause_status_clear_do_not_enqueue_and_session_scoped(tmp_path):
    manager = RuntimeManager(store=SupergoalStore(db_path=tmp_path / "state.db"))
    handler = SupergoalCommandHandler(manager)
    ctx_a = CommandCtx("a")
    ctx_b = CommandCtx("b")
    await handler(ctx_a, "start mission a")

    await handler(ctx_a, "status")
    await handler(ctx_a, "pause")
    await handler(ctx_a, "pause")
    await handler(ctx_b, "status")
    await handler(ctx_a, "clear")
    await handler(ctx_a, "clear")

    assert len(ctx_a.enqueued) == 1
    assert ctx_b.enqueued == []
    assert manager.load_state_for_session("b") is None
    assert manager.load_state_for_session("a").status == "cleared"


@pytest.mark.asyncio
async def test_resume_and_replan_enqueue_real_followup(tmp_path):
    manager = RuntimeManager(store=SupergoalStore(db_path=tmp_path / "state.db"))
    handler = SupergoalCommandHandler(manager)
    ctx = CommandCtx("sess")
    await handler(ctx, "start mission")
    await handler(ctx, "pause")

    await handler(ctx, "resume")
    await handler(ctx, "replan")

    assert len(ctx.enqueued) == 3
    assert manager.load_state_for_session("sess").status == "active"


def test_policy_supervised_blocks_contract_violations_full_auto_allows():
    contract = PermissionContract(
        filesystem_allowlist=["/tmp/allowed"],
        network_allowlist=["allowed.example"],
        destructive_actions="deny",
    )

    denied = PolicyGuard.pre_tool_call("write_file", {"path": "/tmp/other/x"}, contract, mode="supervised")
    auto = PolicyGuard.pre_tool_call("write_file", {"path": "/tmp/other/x"}, contract, mode="full_auto")

    assert denied.decision == "deny"
    assert auto.decision == "allow"


def test_post_tool_evidence_idempotent_and_redacted(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    state = manager.start("sess", "produce artifact")
    hook = ToolHookHandler(store)

    kwargs = {
        "session_id": "sess",
        "tool_name": "write_file",
        "args": {"path": "/tmp/ok.md", "api_key": "secret-value"},
        "result": {"path": "/tmp/ok.md", "token": "secret-token", "success": True},
        "tool_call_id": "tc1",
        "status": "ok",
    }
    hook.post_tool_call(**kwargs)
    hook.post_tool_call(**kwargs)

    events = store.load_events(state.goal_run_id)
    evidence_events = [event for event in events if event["type"] == "tool_evidence_observed"]
    blob = str(evidence_events)
    assert len(evidence_events) == 1
    assert "secret-value" not in blob
    assert "secret-token" not in blob
    assert "[REDACTED]" not in blob  # raw args/results are not persisted at all.


def test_pre_tool_hook_blocks_and_post_tool_hook_ignores_fake_call_id(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    state = manager.start("sess", "safe run")
    state.permission_contract = {"filesystem_allowlist": ["/tmp/allowed"], "destructive_actions": "deny"}
    manager.save_state(state)
    hook = ToolHookHandler(store)

    directive = hook.pre_tool_call(session_id="sess", tool_name="write_file", args={"path": "/tmp/nope"})
    hook.post_tool_call(session_id="sess", tool_name="write_file", args={"path": "/tmp/allowed/x"}, result="ok")

    assert directive and directive["action"] == "block"
    assert [event for event in store.load_events(state.goal_run_id) if event["type"] == "tool_evidence_observed"] == []


@pytest.mark.asyncio
async def test_start_rejects_active_ordinary_goal(tmp_path):
    manager = RuntimeManager(store=SupergoalStore(db_path=tmp_path / "state.db"))
    handler = SupergoalCommandHandler(manager)
    ctx = CommandCtx("sess")

    with patch("supergoal_runtime.command.ordinary_goal_active", return_value=True):
        result = await handler(ctx, "start conflicting mission")

    assert result.startswith("Cannot start /sgx while ordinary /goal is active")
    assert manager.load_state_for_session("sess") is None
    assert ctx.enqueued == []


def test_restart_recovery_uses_only_plugin_database(tmp_path):
    db_path = tmp_path / "state.db"
    first = RuntimeManager(store=SupergoalStore(db_path=db_path))
    started = first.start("sess", "recover after restart")
    first.pause("sess")

    recovered = RuntimeManager(
        store=SupergoalStore(db_path=db_path),
        judge=lambda *_a, **_k: ("continue", "more work", False),
        critic=lambda *_a, **_k: None,
    )
    state = recovered.load_state_for_session("sess")
    text, prompt = recovered.resume("sess")

    assert state is not None and state.goal_run_id == started.goal_run_id
    assert "active" in text.lower()
    assert prompt and "recover after restart" in prompt


def test_wait_barrier_uses_live_pid_and_releases_dead_pid(tmp_path):
    manager = RuntimeManager(
        store=SupergoalStore(db_path=tmp_path / "state.db"),
        judge=lambda *_a, **_k: ("continue", "more work", False),
        critic=lambda *_a, **_k: None,
    )
    manager.start("sess", "wait for process")
    manager.wait("sess", str(os.getpid()))

    parked = manager.after_turn(
        "sess",
        final_response="still running",
        user_message="[Continuing toward your SUPERGOAL — long-running autonomous mode]",
        turn_id="turn-live",
    )
    assert parked and parked["action"] == "noop"
    parked_state = manager.load_state_for_session("sess")
    assert parked_state is not None and parked_state.turns_used == 0

    state = manager.load_state_for_session("sess")
    assert state is not None
    state.waiting_on_pid = 999_999_999
    manager.save_state(state)
    resumed = manager.after_turn(
        "sess",
        final_response="process finished",
        user_message="[Continuing toward your SUPERGOAL — long-running autonomous mode]",
        turn_id="turn-dead",
    )
    assert resumed and resumed["action"] == "continue"
    resumed_state = manager.load_state_for_session("sess")
    assert resumed_state is not None and resumed_state.waiting_on_pid is None


def test_terminal_blocker_pauses_without_continuation(tmp_path):
    manager = RuntimeManager(
        store=SupergoalStore(db_path=tmp_path / "state.db"),
        judge=lambda *_a, **_k: ("done", "requires user input for account authorization", False),
        critic=lambda *_a, **_k: None,
    )
    manager.start("sess", "deploy service")

    decision = manager.after_turn(
        "sess",
        final_response="Deployment requires user input for account authorization.",
        user_message="[Starting supergoal]\nGoal: deploy service",
        turn_id="turn-blocked",
    )

    assert decision and decision["action"] == "pause"
    assert "continuation_prompt" not in decision
    blocked_state = manager.load_state_for_session("sess")
    assert blocked_state is not None and blocked_state.status == "paused"


def test_real_user_message_preempts_automatic_loop(tmp_path):
    manager = RuntimeManager(
        store=SupergoalStore(db_path=tmp_path / "state.db"),
        judge=lambda *_a, **_k: ("continue", "more work", False),
        critic=lambda *_a, **_k: None,
    )
    manager.start("sess", "long mission")

    decision = manager.after_turn(
        "sess",
        final_response="Answering the user instead.",
        user_message="Please stop and explain the current state",
        turn_id="turn-user",
    )

    assert decision and decision["action"] == "pause"
    preempted_state = manager.load_state_for_session("sess")
    assert preempted_state is not None
    assert "real user message" in str(preempted_state.paused_reason or "")


def test_concurrent_tool_evidence_is_session_scoped_and_redacted(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store)
    state_a = manager.start("a", "mission a")
    state_b = manager.start("b", "mission b")
    hook = ToolHookHandler(store)

    def record(session_id: str, index: int) -> None:
        hook.post_tool_call(
            session_id=session_id,
            tool_name="write_file",
            args={"path": f"/tmp/{session_id}-{index}.md", "api_key": f"secret-{session_id}"},
            result={"path": f"/tmp/{session_id}-{index}.md", "success": True, "token": "secret-token"},
            tool_call_id=f"tc-{session_id}-{index}",
            turn_id=f"turn-{index}",
            status="ok",
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda item: record(*item), [(sid, i) for sid in ("a", "b") for i in range(10)]))

    events_a = [e for e in store.load_events(state_a.goal_run_id) if e["type"] == "tool_evidence_observed"]
    events_b = [e for e in store.load_events(state_b.goal_run_id) if e["type"] == "tool_evidence_observed"]
    assert len(events_a) == len(events_b) == 10
    assert "secret-" not in str(events_a + events_b)
    assert all("/tmp/a-" in str(event) for event in events_a)
    assert all("/tmp/b-" in str(event) for event in events_b)
