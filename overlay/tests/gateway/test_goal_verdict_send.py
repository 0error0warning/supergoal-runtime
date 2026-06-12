"""Tests for gateway /goal verdict-message delivery.

The judge verdict message ("✓ Goal achieved", "⏸ budget exhausted", etc.)
must reach the user after each turn. Before this fix the code checked
``hasattr(adapter, "send_message")`` — but adapters expose ``send()``,
never ``send_message``, so the check always evaluated False and users
never saw verdicts. This test locks in the fix.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.session import SessionEntry, SessionSource, build_session_key


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


class _RecordingAdapter:
    """Minimal adapter that records send() invocations."""

    def __init__(self) -> None:
        self._pending_messages: dict = {}
        self.sends: list[dict] = []
        self.status_cards: list[dict] = []
        self.goal_callback = None

    async def send(self, chat_id: str, content: str, reply_to=None, metadata=None):
        self.sends.append({"chat_id": chat_id, "content": content, "metadata": metadata})

        class _R:
            success = True
            message_id = "mock-msg"

        return _R()

    def register_goal_control_callback(self, callback):
        self.goal_callback = callback

    async def send_goal_status_card(
        self,
        chat_id: str,
        message: str,
        *,
        card=None,
        message_id=None,
        source=None,
        metadata=None,
        native_controls=True,
    ):
        if card is None:
            return await self.send(chat_id, message, metadata=metadata)
        self.status_cards.append({
            "chat_id": chat_id,
            "message": message,
            "card": card,
            "message_id": message_id,
            "metadata": metadata,
            "native_controls": native_controls,
        })

        class _R:
            success = True

        result = _R()
        result.message_id = message_id or "status-card-msg"
        return result


def _make_runner_with_adapter(session_id: str = None):
    from gateway.run import GatewayRunner
    import uuid

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")},
    )
    runner.adapters = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._queued_events = {}

    src = _make_source()
    # Default to a unique session_id so xdist parallel runs on the same worker
    # don't see each other's GoalManager state (DEFAULT_DB_PATH gets frozen at
    # module-import time, defeating per-test HERMES_HOME monkeypatches).
    session_entry = SessionEntry(
        session_key=build_session_key(src),
        session_id=session_id or f"goal-sess-{uuid.uuid4().hex[:8]}",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._generate_session_key.return_value = build_session_key(src)
    runner.session_store.switch_session.return_value = session_entry
    runner._session_db = None

    adapter = _RecordingAdapter()
    runner.adapters[Platform.TELEGRAM] = adapter
    return runner, adapter, session_entry, src


@pytest.mark.asyncio
async def test_goal_verdict_done_sent_via_adapter_send(hermes_home):
    """When the judge says done, the '✓ Goal achieved' message must reach
    the user through the adapter's ``send()`` method."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_entry.session_id)
    mgr.set("ship the feature")

    with patch("hermes_cli.goals.judge_goal", return_value=("done", "the feature shipped", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="I shipped the feature.",
        )
        # fire-and-forget create_task — give the loop a tick
        await asyncio.sleep(0.05)

    assert len(adapter.sends) == 1, f"expected 1 send, got {len(adapter.sends)}: {adapter.sends}"
    msg = adapter.sends[0]
    assert msg["chat_id"] == "c1"
    assert "Goal achieved" in msg["content"]
    assert "the feature shipped" in msg["content"]


@pytest.mark.asyncio
async def test_goal_verdict_continue_enqueues_continuation(hermes_home):
    """When the judge says continue, both the 'continuing' status and the
    continuation-prompt event must be delivered. The continuation prompt is
    routed through the adapter's pending-messages FIFO so the goal loop
    proceeds on the next turn."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_entry.session_id)
    mgr.set("polish the docs")

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "still needs work", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="here's a partial edit",
        )
        await asyncio.sleep(0.05)

    # Status line sent back
    assert len(adapter.sends) == 1
    assert "Continuing toward goal" in adapter.sends[0]["content"]
    # Continuation prompt enqueued for next turn
    assert adapter._pending_messages, "continuation prompt must be enqueued in pending_messages"


@pytest.mark.asyncio
async def test_supergoal_verdict_continue_enqueues_supergoal_continuation_and_updates_state(hermes_home):
    """Supergoal post-turn hook must count the turn and enqueue the SUPERGOAL prompt.

    This catches regressions where `/supergoal` state is created but the actual
    continuation loop does not visibly progress beyond the kickoff turn.
    """
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "needs more verified work", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="round one checkpoint",
        )
        await asyncio.sleep(0.05)

    assert adapter.sends == []

    session_key = build_session_key(src)
    pending = adapter._pending_messages.get(session_key)
    assert pending is not None, "supergoal continuation prompt must be enqueued"
    assert pending.text.startswith("[Continuing toward your SUPERGOAL")
    assert "Supergoal: run the long benchmark" in pending.text

    updated = GoalManager(session_entry.session_id).state
    assert updated is not None
    assert updated.mode == "supergoal"
    assert updated.turns_used == 1
    assert updated.last_verdict == "continue"
    assert updated.last_continuation_kind == "gateway-fifo"
    assert updated.last_continuation_enqueued_at > 0


@pytest.mark.asyncio
async def test_supergoal_continue_notice_is_quiet_but_pause_is_visible(hermes_home):
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager, save_goal

    mgr = GoalManager(session_entry.session_id)
    state = mgr.set("run the long benchmark", max_turns=2, mode="supergoal")

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "still working", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="round one checkpoint",
        )

    assert adapter.sends == []
    assert adapter._pending_messages

    adapter._pending_messages.clear()
    adapter.sends.clear()
    state = GoalManager(session_entry.session_id).state
    state.turns_used = 1
    save_goal(session_entry.session_id, state)

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "still working", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="round two checkpoint",
        )

    assert len(adapter.sends) == 1
    assert "paused" in adapter.sends[0]["content"].lower()
    assert not adapter._pending_messages


@pytest.mark.asyncio
async def test_goal_verdict_budget_exhausted_sends_pause(hermes_home):
    """When the budget is exhausted, a '⏸ Goal paused' message must be sent
    and no further continuation enqueued."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager, save_goal

    mgr = GoalManager(session_entry.session_id, default_max_turns=2)
    state = mgr.set("tiny goal", max_turns=2)
    state.turns_used = 2
    save_goal(session_entry.session_id, state)

    with patch("hermes_cli.goals.judge_goal", return_value=("continue", "keep going", False)):
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="still partial",
        )
        await asyncio.sleep(0.05)

    assert len(adapter.sends) == 1
    content = adapter.sends[0]["content"]
    assert "paused" in content.lower()
    assert "turns used" in content.lower()
    # No continuation enqueued when budget is exhausted
    assert not adapter._pending_messages


@pytest.mark.asyncio
async def test_gateway_supergoal_requires_explicit_start_verb(hermes_home):
    """Gateway /supergoal must not treat arbitrary text as a new long loop."""
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    event = MessageEvent(
        text="/supergoal 再做一轮全面检查。",
        message_type=MessageType.TEXT,
        source=src,
        message_id="accidental-supergoal-msg",
    )

    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "/supergoal start <goal>" in response
    assert GoalManager(session_entry.session_id).state is None
    assert adapter._pending_messages == {}


@pytest.mark.asyncio
async def test_gateway_supergoal_start_sets_and_enqueues(hermes_home):
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    event = MessageEvent(
        text="/supergoal start run the long benchmark",
        message_type=MessageType.TEXT,
        source=src,
        message_id="start-supergoal-msg",
    )

    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "run the long benchmark" in response
    state = GoalManager(session_entry.session_id).state
    assert state is not None
    assert state.mode == "supergoal"
    assert state.goal == "run the long benchmark"
    assert adapter._pending_messages


@pytest.mark.asyncio
async def test_gateway_supergoal_resume_follows_compression_tip(hermes_home):
    """`/supergoal resume` should target the migrated compression child.

    Compression migrates GoalManager state from parent -> child.  If the
    gateway's session-key binding is still on the parent, resume used to load
    the parent's migrated tombstone and refuse to wake the loop.  The command
    path now resolves SessionDB.get_compression_tip() first, rewrites the
    session-key binding, and enqueues the continuation under the child state.
    """
    from gateway.platforms.base import MessageEvent, MessageType
    from hermes_cli.goals import GoalManager, save_goal

    runner, adapter, parent_entry, src = _make_runner_with_adapter(session_id="goal-parent")
    child_entry = SessionEntry(
        session_key=parent_entry.session_key,
        session_id="goal-child",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        origin=src,
    )

    class _TipDB:
        def get_compression_tip(self, session_id):
            return "goal-child" if session_id == "goal-parent" else session_id

    runner._session_db = _TipDB()
    runner.session_store.switch_session.return_value = child_entry

    mgr = GoalManager("goal-child")
    mgr.set("continue after compression", max_turns=5, mode="supergoal")
    assert mgr.pause(reason="test-paused") is not None
    save_goal("goal-child", mgr.state)

    # Parent has only the migration tombstone, which must not control resume.
    parent_mgr = GoalManager("goal-parent")
    parent_mgr.set("stale parent", max_turns=5, mode="supergoal")
    parent_state = parent_mgr.state
    parent_state.status = "migrated"
    parent_state.paused_reason = "migrated to goal-child (compression)"
    save_goal("goal-parent", parent_state)

    event = MessageEvent(
        text="/supergoal resume",
        message_type=MessageType.TEXT,
        source=src,
        message_id="resume-after-compression-msg",
    )

    from gateway.run import GatewayRunner

    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "resumed" in response.lower()
    runner.session_store.switch_session.assert_called_once_with(parent_entry.session_key, "goal-child")
    assert GoalManager("goal-child").state.status == "active"
    assert GoalManager("goal-parent").state.status == "migrated"
    assert adapter._pending_messages, "resume should enqueue continuation on the migrated child"
    queued = next(iter(adapter._pending_messages.values()))
    assert "continue after compression" in queued.text


@pytest.mark.asyncio
async def test_gateway_supergoal_resume_enqueues_continuation(hermes_home):
    """`/supergoal resume` must wake the loop, not only set status=active."""
    from gateway.platforms.base import MessageEvent, MessageType
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")
    mgr.pause()
    # Regression: critic-failure auto-pause stores a failure latch. Resume must
    # clear it, otherwise state reload during record_continuation_enqueued()
    # normalizes back to paused and the queued synthetic turn gets dropped.
    mgr.state.consecutive_critic_failures = 3
    from hermes_cli.goals import save_goal
    save_goal(session_entry.session_id, mgr.state)

    event = MessageEvent(
        text="/supergoal resume",
        message_type=MessageType.TEXT,
        source=src,
        message_id="resume-msg",
    )

    from gateway.run import GatewayRunner

    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "resumed" in response.lower()
    session_key = build_session_key(src)
    pending = adapter._pending_messages.get(session_key)
    assert pending is not None, "resume must enqueue the next supergoal turn"
    assert pending.text.startswith("[Continuing toward your SUPERGOAL")
    assert "Supergoal: run the long benchmark" in pending.text

    updated = GoalManager(session_entry.session_id).state
    assert updated is not None
    assert updated.status == "active"
    assert updated.last_continuation_kind == "gateway-resume"
    assert updated.last_continuation_enqueued_at > 0


@pytest.mark.asyncio
async def test_gateway_goal_and_supergoal_controls_do_not_cross_talk(hermes_home):
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    mgr = GoalManager(session_entry.session_id)
    mgr.set("polish the docs")

    super_status = MessageEvent(
        text="/supergoal status",
        message_type=MessageType.TEXT,
        source=src,
        message_id="super-status-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, super_status, super_mode=True)
    assert response == "A goal is active; use /goal status."

    super_pause = MessageEvent(
        text="/supergoal pause",
        message_type=MessageType.TEXT,
        source=src,
        message_id="super-pause-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, super_pause, super_mode=True)
    assert response == "A goal is active; use /goal status."
    assert GoalManager(session_entry.session_id).state.status == "active"

    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")

    goal_status = MessageEvent(
        text="/goal status",
        message_type=MessageType.TEXT,
        source=src,
        message_id="goal-status-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, goal_status)
    assert response == "A supergoal is active; use /supergoal status."

    goal_clear = MessageEvent(
        text="/goal clear",
        message_type=MessageType.TEXT,
        source=src,
        message_id="goal-clear-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, goal_clear)
    assert response == "A supergoal is active; use /supergoal status."
    state = GoalManager(session_entry.session_id).state
    assert state is not None
    assert state.mode == "supergoal"
    assert state.status == "active"

    super_clear = MessageEvent(
        text="/supergoal clear",
        message_type=MessageType.TEXT,
        source=src,
        message_id="super-clear-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, super_clear, super_mode=True)
    assert "cleared" in response.lower()
    cleared_mgr = GoalManager(session_entry.session_id)
    assert cleared_mgr.state is not None
    assert cleared_mgr.state.status == "cleared"
    assert cleared_mgr.has_goal() is False


@pytest.mark.asyncio
async def test_gateway_supergoal_clear_archives_existing_native_card(hermes_home):
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")
    mgr.set_status_card_message_id("telegram:c1", "card-123")

    event = MessageEvent(
        text="/supergoal clear",
        message_type=MessageType.TEXT,
        source=src,
        message_id="super-clear-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "cleared" in response.lower()
    assert adapter.status_cards, "clear should edit/archive the existing card before state removal"
    archived = adapter.status_cards[-1]
    assert archived["message_id"] == "card-123"
    assert archived["native_controls"] is False
    assert archived["card"].controls == []
    assert "archived" in archived["message"].lower()
    assert GoalManager(session_entry.session_id).get_status_card_message_id("telegram:c1") == ""


@pytest.mark.asyncio
async def test_gateway_supergoal_archive_control_works_after_done(hermes_home):
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")
    mgr.set_status_card_message_id("telegram:c1", "card-456")
    mgr.mark_done("verified complete")

    event = MessageEvent(
        text="/supergoal clear",
        message_type=MessageType.TEXT,
        source=src,
        message_id="super-archive-msg",
    )
    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    assert "cleared" in response.lower()
    assert adapter.status_cards[-1]["message_id"] == "card-456"
    assert adapter.status_cards[-1]["native_controls"] is False
    assert GoalManager(session_entry.session_id).has_goal() is False


@pytest.mark.asyncio
async def test_gateway_supergoal_control_callback_registered_on_connect(hermes_home):
    from gateway.run import GatewayRunner

    runner, adapter, _session_entry, _src = _make_runner_with_adapter()
    adapter.goal_callback = None

    GatewayRunner._register_supergoal_control_callback(runner, adapter)

    assert adapter.goal_callback == runner._handle_supergoal_ui_action


@pytest.mark.asyncio
async def test_stale_synthetic_supergoal_continuation_dropped_before_agent_run(hermes_home, monkeypatch):
    """A queued continuation must not run after the supergoal was paused/cleared.

    The post-turn drain path already had this guard; this locks the separate
    adapter-started background path that calls _handle_message_with_agent
    directly for a pending event.
    """
    from gateway.platforms.base import MessageEvent, MessageType
    from gateway.run import GatewayRunner
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    runner._session_db = None
    mgr = GoalManager(session_entry.session_id)
    mgr.set("run the long benchmark", max_turns=240, mode="supergoal")
    mgr.pause("test pause before queued continuation starts")

    called = False

    async def _should_not_run(*args, **kwargs):
        nonlocal called
        called = True
        return {"final_response": "should not run"}

    monkeypatch.setattr(GatewayRunner, "_run_agent", _should_not_run, raising=False)
    event = MessageEvent(
        text="[Continuing toward your SUPERGOAL — long-running autonomous mode]\nSupergoal: run the long benchmark",
        message_type=MessageType.TEXT,
        source=src,
        message_id=None,
    )

    result = await GatewayRunner._handle_message_with_agent(
        runner,
        event,
        src,
        session_entry.session_key,
        1,
    )

    assert result is None
    assert called is False


@pytest.mark.asyncio
async def test_gateway_goal_resume_enqueues_goal_text(hermes_home):
    """Plain `/goal resume` should also wake a paused goal."""
    from gateway.platforms.base import MessageEvent, MessageType
    from hermes_cli.goals import GoalManager

    runner, adapter, session_entry, src = _make_runner_with_adapter()
    mgr = GoalManager(session_entry.session_id)
    mgr.set("polish the docs")
    mgr.pause()

    event = MessageEvent(
        text="/goal resume",
        message_type=MessageType.TEXT,
        source=src,
        message_id="resume-msg",
    )

    from gateway.run import GatewayRunner

    response = await GatewayRunner._handle_goal_command(runner, event)

    assert "resumed" in response.lower()
    session_key = build_session_key(src)
    pending = adapter._pending_messages.get(session_key)
    assert pending is not None, "resume must enqueue the next goal turn"
    assert pending.text == "polish the docs"

    updated = GoalManager(session_entry.session_id).state
    assert updated is not None
    assert updated.status == "active"


@pytest.mark.asyncio
async def test_goal_verdict_skipped_when_no_active_goal(hermes_home):
    """No goal set → the hook is a no-op. Nothing is sent, nothing enqueued."""
    runner, adapter, session_entry, src = _make_runner_with_adapter()

    await runner._post_turn_goal_continuation(
        session_entry=session_entry,
        source=src,
        final_response="anything",
    )
    await asyncio.sleep(0.05)

    assert adapter.sends == []
    assert adapter._pending_messages == {}


@pytest.mark.asyncio
async def test_goal_verdict_survives_adapter_without_send(hermes_home):
    """Bad adapter (no ``send`` attribute) must not crash the judge hook."""
    runner, _adapter, session_entry, src = _make_runner_with_adapter()

    from hermes_cli.goals import GoalManager

    GoalManager(session_entry.session_id).set("survive missing send")

    class _NoSendAdapter:
        def __init__(self):
            self._pending_messages: dict = {}

    runner.adapters[Platform.TELEGRAM] = _NoSendAdapter()

    with patch("hermes_cli.goals.judge_goal", return_value=("done", "ok", False)):
        # must not raise
        await runner._post_turn_goal_continuation(
            session_entry=session_entry,
            source=src,
            final_response="whatever",
        )
        await asyncio.sleep(0.05)
