import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource
from hermes_cli import goals


class _FakeSessionEntry:
    session_id = "sid-gateway-supergoal-config"


class _FakeSessionStore:
    def __init__(self):
        self.entry = _FakeSessionEntry()

    def get_or_create_session(self, source):
        return self.entry

    def _generate_session_key(self, source):
        return "agent:main:discord:channel:supergoal-config"


@pytest.mark.asyncio
async def test_gateway_supergoal_uses_goals_super_max_turns_from_full_config(tmp_path, monkeypatch):
    """Gateway /supergoal should honor top-level goals.super_max_turns from config.yaml."""
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / "config.yaml").write_text("goals:\n  max_turns: 7\n  super_max_turns: 123\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    goals._DB_CACHE.clear()

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.DISCORD: PlatformConfig(enabled=True, token="token")}
    )
    runner.session_store = _FakeSessionStore()
    runner.adapters = {}
    runner._queued_events = {}

    event = MessageEvent(
        text="/supergoal start ship the benchmark",
        message_type=MessageType.TEXT,
        source=SessionSource(
            platform=Platform.DISCORD,
            chat_id="chat-supergoal-config",
            chat_type="channel",
            user_id="user-supergoal-config",
        ),
        message_id="msg-supergoal-config",
    )

    response = await GatewayRunner._handle_goal_command(runner, event, super_mode=True)

    try:
        assert "⊙ Goal set (123-turn budget): ship the benchmark" in response
        state = goals.GoalManager("sid-gateway-supergoal-config").state
        assert state is not None
        assert state.max_turns == 123
        assert state.mode == "supergoal"
    finally:
        goals._DB_CACHE.clear()
