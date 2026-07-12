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
    enqueued = []

    async def enqueue_followup(prompt):
        enqueued.append(prompt)
        return True

    command_ctx = SimpleNamespace(session_id="sess-1", enqueue_followup=enqueue_followup)

    result = await entry["handler"](command_ctx, "start Phase 5 mission")

    assert result.startswith("Started /sgx supergoal")
    assert enqueued
    assert host.controllers
    directive = host.controllers[0][2](
        SimpleNamespace(session_id="sess-1", final_response="not done", turn_id="t1")
    )
    assert directive.action == "continue"
    assert directive.continuation_prompt
    assert directive.dedupe_key


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
    plugin_runtime = manager._turn_controllers[0]["handler"].__self__
    plugin_runtime.manager.judge = lambda *_a, **_k: ("continue", "needs work", False)
    plugin_runtime.manager.critic = lambda *_a, **_k: None

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
        user_message="[Starting supergoal]\nGoal: host mission",
        final_response="started",
        interrupted=False,
        background_processes=[],
    )

    with patch("hermes_cli.plugins._plugin_manager", manager):
        result = await dispatch_plugin_command_async("sgx", "start host mission", command_ctx)
        directive = await invoke_turn_controllers(turn_ctx)

    assert result.startswith("Started /sgx supergoal")
    assert directive.action == "continue"
    assert directive.continuation_prompt


def test_sgx_controller_noops_when_not_started():
    host = HostCtx()
    register(host)

    directive = host.controllers[0][2](SimpleNamespace(session_id="missing", final_response=""))

    assert directive is None


@pytest.mark.asyncio
async def test_sgx_binding_follows_compression_rotation():
    host = HostCtx()
    register(host)
    enqueued = []

    async def enqueue_followup(prompt):
        enqueued.append(prompt)
        return True

    command_ctx = SimpleNamespace(session_id="old-session", enqueue_followup=enqueue_followup)
    await host.commands["sgx"]["handler"](command_ctx, "start compression mission")

    host.hooks["on_session_rotate"][0](
        old_session_id="old-session",
        new_session_id="new-session",
        reason="compression",
        parent_session_id="old-session",
        surface="gateway",
    )

    assert host.controllers[0][2](SimpleNamespace(session_id="old-session", final_response="")) is None
    directive = host.controllers[0][2](SimpleNamespace(session_id="new-session", final_response="not done", turn_id="t2"))
    assert directive.action == "continue"
    assert directive.dedupe_key


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
    assert manager._plugin_commands["sgx"]["busy_safe_subcommands"] == (
        "",
        "status",
        "pause",
        "resume",
        "clear",
        "wait",
        "unwait",
        "replan",
    )
    assert manager._turn_controllers
