"""Execute against the real Hermes plugin registry, not a synthetic dispatcher."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from supergoal_runtime.plugin import register

host = pytest.importorskip("hermes_cli.plugins")


def make_policy(root: Path):
    (root / "work").mkdir()
    for name, value in [("good", "ready"), ("bad", "wrong")]:
        (root / name).mkdir()
        (root / name / "result.txt").write_text(value)
    (root / "check.py").write_text("from pathlib import Path\nraise SystemExit(0 if Path('result.txt').read_text()=='ready' else 1)\n")
    path = root / "policy.json"
    path.write_text(json.dumps({"schema": 1, "outcome": "Deliver ready in result.txt", "scope": "Exact text",
        "workspace": "work", "criteria": {"result": "result.txt is ready"}, "artifacts": ["result.txt"],
        "checks": [{"id": "text", "covers": ["result"], "timeout": 2,
                    "argv": [sys.executable, "{policy_dir}/check.py"], "source_files": ["check.py"]}],
        "calibration": [{"path": "good", "expect": "pass"}, {"path": "bad", "expect": "fail"}]}))
    return path


def setup_host():
    manager = host.PluginManager()
    manager._discovered = True
    ctx = host.PluginContext(host.PluginManifest(name="supergoal-runtime"), manager)
    with patch.object(ctx, "get_config", return_value="v2"):
        register(ctx)
    entries = manager._turn_controllers.values()
    runtime = next(iter(entries))["handler"].__self__
    return manager, runtime


@pytest.mark.asyncio
async def test_real_host_start_continue_finish_and_restart_recovery(tmp_path):
    policy = make_policy(tmp_path)
    manager, runtime = setup_host()
    queued = []

    async def enqueue(prompt, **metadata):
        assert host.claim_plugin_continuation(plugin_id=metadata["plugin_id"], controller_name=metadata["controller_name"],
            session_id="v2-session", token=metadata["continuation_token"], state_version=metadata["state_version"], manager=manager)
        queued.append(prompt)
        return True

    ctx = host.CommandContext("gateway", "v2-session", "telegram", None, "task", {}, enqueue)
    with patch.object(host, "_plugin_manager", manager):
        answer = await host.dispatch_plugin_command_async("supergoal", f"start {policy}", ctx)
        assert "First turn queued" in answer
        assert len(queued) == 1
        (tmp_path / "work/result.txt").write_text("wrong")
        turn = host.TurnControlContext("gateway", "v2-session", "telegram", None, "task", "t1", queued[0], "done", False, [])
        directive = await host.invoke_turn_controllers(turn)
        assert directive.action == "continue"
    # Crash after commit, before next dispatch: a newly loaded host recovers the same envelope.
    manager2, runtime2 = setup_host()
    recovered = host.recover_plugin_continuations(manager=manager2)
    assert len(recovered) == 1
    assert recovered[0]["token"] == directive.continuation_token
    assert host.claim_plugin_continuation(plugin_id="supergoal-runtime", controller_name="supergoal-runtime",
        session_id="v2-session", token=recovered[0]["token"], state_version=recovered[0]["state_version"], manager=manager2)
    assert not runtime2.claim_continuation(session_id="v2-session", token=recovered[0]["token"])
    (tmp_path / "work/result.txt").write_text("ready")
    turn2 = host.TurnControlContext("gateway", "v2-session", "telegram", None, "task", "t2", recovered[0]["prompt"], "done", False, [])
    with patch.object(host, "_plugin_manager", manager2):
        assert (await host.invoke_turn_controllers(turn2)).action == "done"
    assert runtime2.store.state(runtime2.store.bound("v2-session"))["status"] == "succeeded"
    assert host.recover_plugin_continuations(manager=manager2) == []


@pytest.mark.asyncio
async def test_real_host_executor_dispute_cannot_self_approve(tmp_path):
    policy = make_policy(tmp_path)
    manager, runtime = setup_host()
    queued = []

    async def enqueue(prompt, **metadata):
        queued.append(prompt)
        return host.claim_plugin_continuation(plugin_id="supergoal-runtime", controller_name="supergoal-runtime",
            session_id="review-session", token=metadata["continuation_token"], state_version=metadata["state_version"], manager=manager)

    ctx = host.CommandContext("gateway", "review-session", "telegram", None, "task", {}, enqueue)
    with patch.object(host, "_plugin_manager", manager):
        await host.dispatch_plugin_command_async("supergoal", f"start {policy}", ctx)
        (tmp_path / "work/result.txt").write_text("ready")
        turn = host.TurnControlContext("gateway", "review-session", "telegram", None, "task", "t1", queued[0],
            "<supergoal-dispute>These checks omit the requested behavior.</supergoal-dispute>", False, [])
        result = await host.invoke_turn_controllers(turn)
        assert result.action == "pause"
        assert "contract_disputed" in await host.dispatch_plugin_command_async("supergoal", "status", ctx)
        assert "cannot resume" in await host.dispatch_plugin_command_async("supergoal", "resume", ctx)
    assert host.recover_plugin_continuations(manager=manager) == []
