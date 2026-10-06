"""A real, isolated Hermes SDK process; never runs model tools on the host."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("control", type=Path)
    parser.add_argument("--tool-smoke", action="store_true")
    args = parser.parse_args()
    control = json.loads(args.control.read_text())
    home = Path(os.environ["HERMES_HOME"])
    plugin = home / "plugins/harbor-task"
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.yaml").write_text("name: harbor-task\nversion: 0.1.0\ndescription: Isolated Harbor task tools\n")
    (plugin / "__init__.py").write_text("from hermes_bridge import register\n")
    provider = control["provider"]
    config = {
        "model": {"default": "devin/swe-2", "provider": "custom:Devin", "base_url": provider["base_url"]},
        "custom_providers": [{"name": "Devin", **provider}], "fallback_providers": [],
        "plugins": {"enabled": ["harbor-task"]},
        "terminal": {"backend": "harbor_task", "cwd": control["cwd"], "timeout": 120},
        "memory": {"memory_enabled": False, "user_profile_enabled": False},
        "compression": {"enabled": False}, "mcp_servers": {},
        "agent": {"max_turns": control["max_calls"]},
    }
    (home / "config.yaml").write_text(yaml.safe_dump(config))
    # Import only after the isolated home/backend configuration has been written.
    from run_agent import AIAgent

    agent = AIAgent(model="devin/swe-2", provider="custom:Devin", requested_provider="custom:Devin",
                    base_url=provider["base_url"], api_key=provider["api_key"], api_mode="codex_responses",
                    enabled_toolsets=["terminal", "file"], max_iterations=control["max_calls"],
                    max_tokens=6000, run_budget_seconds=control["seconds"],
                    quiet_mode=True, skip_memory=True, skip_context_files=True, skip_background_review=True,
                    session_id=control["session_id"], fallback_model=None)
    started = time.monotonic()
    try:
        if args.tool_smoke:
            from tools.terminal_tool import terminal_tool
            from tools.file_tools import read_file_tool, write_file_tool
            task_id = control["session_id"]
            marker = "/tmp/supergoal-bridge-smoke.txt"
            results = {
                "terminal": json.loads(terminal_tool("pwd; test ! -e /var/lib/supergoal-lab/private/model.json && test ! -S /var/lib/supergoal-lab/run/docker.sock && echo isolated", task_id=task_id)),
                "write": json.loads(write_file_tool(marker, "hermes_bridge_smoke", task_id=task_id)),
                "read": json.loads(read_file_tool(marker, task_id=task_id)),
                "hidden_read": json.loads(read_file_tool(control["host_marker"], task_id=task_id)),
                "timeout": json.loads(terminal_tool("sleep 10", timeout=1, task_id=task_id)),
            }
            result = {"tool_smoke": results, "api_calls": 0}
        else:
            result = agent.run_conversation(control["prompt"], conversation_history=control.get("history"))
        result["research_episode_seconds"] = time.monotonic() - started
        output = Path(control["output"])
        temp = output.with_suffix(".tmp")
        temp.write_text(json.dumps(result, ensure_ascii=False, default=str))
        temp.replace(output)
        print(json.dumps({"completed": result.get("completed"), "api_calls": result.get("api_calls"),
                          "seconds": result["research_episode_seconds"]}), flush=True)
    finally:
        agent.close()


if __name__ == "__main__":
    main()
