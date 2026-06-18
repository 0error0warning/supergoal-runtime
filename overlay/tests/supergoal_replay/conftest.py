from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load_trace(name: str) -> list[dict[str, Any]]:
    path = FIXTURE_DIR / name
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


class ReplayResult:
    def __init__(self) -> None:
        self.managers: dict[str, Any] = {}
        self.decisions: list[dict[str, Any]] = []
        self.policy_decisions: list[dict[str, Any]] = []
        self.goal_run_ids: list[str] = []

    @property
    def current(self):
        if not self.managers:
            return None
        return list(self.managers.values())[-1]


def run_trace(events: list[dict[str, Any]]) -> ReplayResult:
    from hermes_cli.goals import GoalManager, migrate_goal_state
    from hermes_cli.supergoal.evidence import record_tool_evidence
    from hermes_cli.supergoal.policy import PermissionContract, PolicyGuard

    result = ReplayResult()
    current_session = ""

    for event in events:
        op = event["op"]
        session_id = event.get("session_id") or current_session
        if op == "set":
            current_session = event["session_id"]
            mgr = GoalManager(session_id=current_session)
            state = mgr.set(event["goal"], max_turns=event.get("max_turns", 20), mode="supergoal")
            result.managers[current_session] = mgr
            result.goal_run_ids.append(state.goal_run_id)
        elif op == "tool_evidence":
            record_tool_evidence(
                session_id=session_id,
                tool_name=event["tool_name"],
                args=event.get("args") or {},
                result=event.get("result") or "",
                tool_call_id=event.get("tool_call_id") or "tc-replay",
                turn_id=event.get("turn_id") or "turn-replay",
                status=event.get("status") or "ok",
            )
        elif op == "assistant_turn":
            mgr = result.managers.get(session_id) or GoalManager(session_id=session_id)
            result.managers[session_id] = mgr
            current_session = session_id
            with patch("hermes_cli.goals.judge_goal", return_value=tuple(event["judge"])), patch(
                "hermes_cli.goals.critic_supergoal", return_value=event.get("critic") or {}
            ):
                decision = mgr.evaluate_after_turn(event.get("response") or "")
            result.decisions.append(decision)
        elif op == "rotate":
            old = event["old_session_id"]
            new = event["new_session_id"]
            state = migrate_goal_state(old, new, reason=event.get("reason") or "replay")
            current_session = new
            result.managers[new] = GoalManager(session_id=new)
            if state is not None:
                result.goal_run_ids.append(state.goal_run_id)
        elif op == "policy_call":
            mgr = result.managers.get(session_id) or GoalManager(session_id=session_id)
            state = mgr.state
            assert state is not None
            contract = PermissionContract.from_mapping(state.permission_contract)
            policy = PolicyGuard.pre_tool_call(
                state.goal_run_id,
                state.action_proposal,
                event["tool_name"],
                event.get("args") or {},
                contract,
                mode=state.permission_mode,
                task_id=event.get("task_id") or "default",
            )
            result.policy_decisions.append(policy.__dict__)
        else:
            raise AssertionError(f"unknown replay op: {op}")
    return result


@pytest.fixture
def replay():
    return run_trace


@pytest.fixture
def trace():
    return load_trace
