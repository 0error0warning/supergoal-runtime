from __future__ import annotations

import json
from pathlib import Path

from supergoal_runtime.policy import ToolHookHandler
from supergoal_runtime.runtime import RuntimeManager
from supergoal_runtime.store import SupergoalStore


FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def load_trace(name: str) -> list[dict]:
    return [json.loads(line) for line in (FIXTURE_DIR / name).read_text().splitlines() if line.strip()]


class Replay:
    def __init__(self, tmp_path):
        self.judges: list[tuple] = []
        self.critics: list[dict | None] = []
        self.store = SupergoalStore(db_path=tmp_path / "state.db")
        self.manager = RuntimeManager(store=self.store, judge=self.judge, critic=self.critic)
        self.hooks = ToolHookHandler(self.store)
        self.decisions: list[dict] = []
        self.current_session = ""

    def judge(self, *_args, **_kwargs):
        return self.judges.pop(0) if self.judges else ("continue", "default continue", False)

    def critic(self, *_args, **_kwargs):
        return self.critics.pop(0) if self.critics else None

    def run(self, trace: list[dict]) -> "Replay":
        for op in trace:
            kind = op["op"]
            if kind == "set":
                self.current_session = op["session_id"]
                self.manager.start(op["session_id"], op["goal"], max_turns=op.get("max_turns"))
            elif kind == "tool_evidence":
                self.hooks.post_tool_call(
                    session_id=self.current_session,
                    tool_name=op["tool_name"],
                    args=op.get("args") or {},
                    result=op.get("result"),
                    tool_call_id=op.get("tool_call_id") or "",
                    status=op.get("status") or "ok",
                )
            elif kind == "assistant_turn":
                session_id = op.get("session_id") or self.current_session
                self.current_session = session_id
                self.judges.append(tuple(op.get("judge") or ("continue", "needs more", False)))
                self.critics.append(op.get("critic"))
                decision = self.manager.after_turn(
                    session_id,
                    final_response=op.get("response") or "",
                    turn_id=f"turn-{len(self.decisions) + 1}",
                )
                if decision:
                    self.decisions.append(decision)
            elif kind == "rotate":
                goal_run_id = self.store.rotate_session_binding(op["old_session_id"], op["new_session_id"], reason=op.get("reason") or "compression")
                assert goal_run_id
                self.current_session = op["new_session_id"]
        return self


def test_real_bitget_fixture_replays_infra_gate_invariant(tmp_path):
    replay = Replay(tmp_path).run(load_trace("bitget_20260608_trace.jsonl"))
    state = replay.manager.load_state_for_session("replay-bitget")

    assert state is not None
    assert state.hard_gate_reason
    assert "infra_engineering" in state.hard_gate_reason
    assert state.action_proposal.action_class == "hypothesis_generation"
    assert state.action_proposal.target_gate_id == "SG-1"
    assert any(decision["action"] == "continue" for decision in replay.decisions)


def test_real_completion_fixture_done_without_continuation(tmp_path):
    replay = Replay(tmp_path).run(load_trace("ai6_completion_gate_conflict.jsonl"))
    state = replay.manager.load_state_for_session("replay-ai6")
    decision = replay.decisions[-1]

    assert state is not None
    assert state.status == "done"
    assert decision["action"] == "done"
    assert "continuation_prompt" not in decision or decision["continuation_prompt"] is None
    assert state.should_replan is False
    assert not state.next_best_action


def test_real_compression_fixture_preserves_goal_run_id_and_continues(tmp_path):
    replay = Replay(tmp_path).run(load_trace("compression_split_trace.jsonl"))
    old_id = replay.store.get_goal_run_id("replay-compress-old")
    new_id = replay.store.get_goal_run_id("replay-compress-new")
    state = replay.manager.load_state_for_session("replay-compress-new")

    assert old_id == new_id
    assert state is not None
    assert state.goal_run_id == new_id
    assert state.status == "active"
    assert replay.decisions[-1]["action"] == "continue"
