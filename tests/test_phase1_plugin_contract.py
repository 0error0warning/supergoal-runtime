from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from supergoal_runtime.plugin import register


@pytest.fixture(autouse=True)
def _isolate_hermes_home(tmp_path, monkeypatch):
    """Every plugin contract test uses a disposable profile home."""

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))


class HostCtx:
    def __init__(self):
        self.commands = {}
        self.controllers = []
        self.hooks = {}

    def register_command(self, name, handler, **meta):
        self.commands[name] = {"handler": handler, **meta}

    def register_turn_controller(self, name, handler, priority=100):
        self.controllers.append((priority, name, handler))

    def register_hook(self, name, handler):
        self.hooks.setdefault(name, []).append(handler)


@pytest.mark.asyncio
async def test_sgx_start_uses_context_session_id_and_controller_continues():
    host = HostCtx()
    register(host)

    entry = host.commands["sgx"]
    assert entry["context_aware"] is True
    command_ctx = SimpleNamespace(session_id="sess-1")

    result = await entry["handler"](command_ctx, "start")

    assert result == "sgx started for session sess-1"
    assert host.controllers
    directive = host.controllers[0][2](
        SimpleNamespace(session_id="sess-1")
    )
    assert directive.action == "continue"
    assert directive.continuation_prompt
    assert directive.dedupe_key == "sgx:sess-1:1"


@pytest.mark.asyncio
async def test_sgx_runs_through_real_host_command_and_controller_abi():
    from hermes_cli.plugins import (
        CommandContext,
        PluginContext,
        PluginManager,
        PluginManifest,
        TurnControlContext,
        dispatch_plugin_command_async,
        invoke_turn_controllers,
    )

    manager = PluginManager()
    manager._discovered = True
    register(PluginContext(PluginManifest(name="supergoal-runtime"), manager))

    async def enqueue_followup(_prompt: str) -> bool:
        return True

    command_ctx = CommandContext(
        surface="gateway",
        session_id="host-sess",
        platform="telegram",
        source=None,
        task_id="task",
        metadata={},
        enqueue_followup=enqueue_followup,
    )
    turn_ctx = TurnControlContext(
        surface="gateway",
        session_id="host-sess",
        platform="telegram",
        source=None,
        task_id="task",
        turn_id="turn-1",
        user_message="/sgx start",
        final_response="started",
        interrupted=False,
        background_processes=[],
    )

    with patch("hermes_cli.plugins._plugin_manager", manager):
        result = await dispatch_plugin_command_async("sgx", "start", command_ctx)
        directive = await invoke_turn_controllers(turn_ctx)

    assert result == "sgx started for session host-sess"
    assert directive.action == "continue"
    assert directive.continuation_prompt


def test_sgx_controller_noops_when_not_started():
    host = HostCtx()
    register(host)

    directive = host.controllers[0][2](SimpleNamespace(session_id="missing"))

    assert directive is None


@pytest.mark.asyncio
async def test_sgx_binding_follows_compression_rotation():
    host = HostCtx()
    register(host)
    command_ctx = SimpleNamespace(session_id="old-session")
    await host.commands["sgx"]["handler"](command_ctx, "start")

    host.hooks["on_session_rotate"][0](
        old_session_id="old-session",
        new_session_id="new-session",
        reason="compression",
        parent_session_id="old-session",
        surface="gateway",
    )

    assert host.controllers[0][2](SimpleNamespace(session_id="old-session")) is None
    directive = host.controllers[0][2](SimpleNamespace(session_id="new-session"))
    assert directive.action == "continue"
    assert directive.dedupe_key == "sgx:new-session:1"


def test_directory_plugin_loads_through_hermes_namespace():
    from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest

    root = Path(__file__).resolve().parents[1]
    manager = PluginManager()
    manifest = PluginManifest(
        name="supergoal-runtime",
        key="supergoal-runtime",
        path=str(root),
        source="user",
    )

    module = manager._load_directory_module(manifest)
    module.register(PluginContext(manifest, manager))

    assert "sgx" in manager._plugin_commands
    assert manager._plugin_commands["sgx"]["context_aware"] is True
    assert manager._turn_controllers
