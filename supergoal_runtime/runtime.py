"""Plugin-owned Supergoal runtime manager.

This module owns persistence orchestration for the standalone plugin. It does
not import Hermes internals; host-facing wrappers live in ``plugin.py`` and
``command.py``.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Callable

from .domain import DEFAULT_MAX_TURNS, GoalEvent, GoalState, SupergoalActionProposal, _infer_terminal_blocker_status
from .evaluators import apply_supergoal_critic
from .gates import first_blocking_failure, reconcile_done_evidence_gates, update_supergoal_gates
from .projection import apply_events_to_state, extract_observation_events
from .prompts import build_continuation_prompt
from .rendering import status_card, status_line
from .store import SupergoalStore

JudgeCallback = Callable[..., tuple[Any, ...]]
CriticCallback = Callable[[GoalState, str], dict[str, Any] | None]


def _event(event_type: str, *, turn: int = 0, summary: str = "", data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "ts": time.time(),
        "type": event_type,
        "turn": int(turn or 0),
        "summary": summary,
        "data": dict(data or {}),
    }


def state_to_record(state: GoalState) -> dict[str, Any]:
    return asdict(state)


def state_from_record(record: dict[str, Any] | None) -> GoalState | None:
    if not record:
        return None
    return GoalState.from_json(__import__("json").dumps(record, ensure_ascii=False))


def _pid_alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class RuntimeManager:
    def __init__(
        self,
        *,
        store: SupergoalStore | None = None,
        judge: JudgeCallback | None = None,
        critic: CriticCallback | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> None:
        self.store = store or SupergoalStore()
        self.judge = judge or (lambda *_a, **_k: ("continue", "no judge callback configured", False))
        self.critic = critic or (lambda *_a, **_k: None)
        self.default_max_turns = int(max_turns or DEFAULT_MAX_TURNS)

    def start(self, session_id: str, goal: str, *, max_turns: int | None = None) -> GoalState:
        goal_text = " ".join(str(goal or "").split())
        if not session_id:
            raise ValueError("session_id is required")
        if not goal_text:
            raise ValueError("goal text is required")
        goal_run_id = f"gr_{uuid.uuid4().hex[:16]}"
        state = GoalState(
            goal=goal_text,
            goal_run_id=goal_run_id,
            mode="supergoal",
            status="active",
            max_turns=int(max_turns or self.default_max_turns),
            created_at=time.time(),
            inferred_user_intent=goal_text,
            success_definition=(
                "Satisfy the mission with tool-backed evidence and verified artifacts; "
                "if success is impossible, produce a concrete blocked or no-edge report."
            ),
        )
        update_supergoal_gates(state)
        self.store.import_run_bundle(
            goal_run_id,
            state_to_record(state),
            bindings=[(session_id, "sgx_start")],
            events=[(_event("started", summary="Supergoal started", data={"session_id": session_id}), "start", 0)],
        )
        return state

    def load_state_for_session(self, session_id: str) -> GoalState | None:
        goal_run_id = self.store.get_goal_run_id(session_id)
        if not goal_run_id or not self.store.is_current_session(session_id):
            return None
        return state_from_record(self.store.load_run(goal_run_id))

    def save_state(self, state: GoalState, events: list[dict[str, Any]] | None = None) -> None:
        self.store.save_run_with_events(state.goal_run_id, state_to_record(state), events or [])

    def status_text(self, session_id: str) -> str:
        return status_card(self.load_state_for_session(session_id)).to_text()

    def pause(self, session_id: str) -> str:
        state = self.load_state_for_session(session_id)
        if state is None:
            return status_line(None)
        if state.status == "paused":
            return status_line(state)
        if state.status == "cleared":
            return status_line(state)
        state.status = "paused"
        state.paused_reason = "paused by user"
        self.save_state(state, [_event("paused", turn=state.turns_used, summary="paused by user")])
        return status_line(state)

    def resume(self, session_id: str) -> tuple[str, str | None]:
        state = self.load_state_for_session(session_id)
        if state is None:
            return status_line(None), None
        if state.status in {"done", "cleared"}:
            return status_line(state), None
        state.status = "active"
        state.paused_reason = None
        update_supergoal_gates(state)
        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        self.save_state(state, [_event("resumed", turn=state.turns_used, summary="resumed by user")])
        return status_line(state), prompt

    def clear(self, session_id: str) -> str:
        state = self.load_state_for_session(session_id)
        if state is None:
            return "No active /sgx supergoal for this session."
        if state.status != "cleared":
            state.status = "cleared"
            state.paused_reason = "cleared by user"
            self.save_state(state, [_event("cleared", turn=state.turns_used, summary="cleared by user")])
        return status_line(state)

    def wait(self, session_id: str, target: str) -> str:
        state = self.load_state_for_session(session_id)
        if state is None:
            return status_line(None)
        raw = str(target or "").strip()
        try:
            pid = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError("wait target must be a positive process PID") from exc
        if pid <= 0:
            raise ValueError("wait target must be a positive process PID")
        state.waiting_on_pid = pid
        state.waiting_on_session = None
        state.waiting_until = 0.0
        state.waiting_reason = f"pid {pid}"
        state.waiting_since = time.time()
        self.save_state(state, [_event("wait_started", turn=state.turns_used, summary=state.waiting_reason)])
        return status_line(state)

    def unwait(self, session_id: str) -> str:
        state = self.load_state_for_session(session_id)
        if state is None:
            return status_line(None)
        state.waiting_on_pid = None
        state.waiting_on_session = None
        state.waiting_until = 0.0
        state.waiting_reason = None
        self.save_state(state, [_event("wait_cleared", turn=state.turns_used, summary="wait cleared")])
        return status_line(state)

    def replan(self, session_id: str) -> tuple[str, str | None]:
        state = self.load_state_for_session(session_id)
        if state is None:
            return status_line(None), None
        state.should_replan = True
        state.replan_count += 1
        state.next_best_action = state.next_best_action or "Replan against the first failed blocking gate."
        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        self.save_state(state, [_event("replan_requested", turn=state.turns_used, summary="replan requested")])
        return status_line(state), prompt

    def is_waiting(self, state: GoalState) -> bool:
        if state.waiting_on_pid is not None:
            if _pid_alive(state.waiting_on_pid):
                return True
            state.waiting_on_pid = None
            state.waiting_reason = None
            state.waiting_since = 0.0
        if state.waiting_on_session:
            return True
        if state.waiting_until and time.time() < state.waiting_until:
            return True
        if state.waiting_until and time.time() >= state.waiting_until:
            state.waiting_until = 0.0
            state.waiting_reason = None
        return False

    def after_turn(
        self,
        session_id: str,
        *,
        final_response: str,
        turn_id: str = "",
        user_message: str = "",
        interrupted: bool = False,
        background_processes: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        state = self.load_state_for_session(session_id)
        if state is None or state.status != "active":
            return None
        message = str(user_message or "").lstrip()
        synthetic_turn = message.startswith("[Starting supergoal]") or message.startswith(
            "[Continuing toward your SUPERGOAL"
        )
        if message and not synthetic_turn:
            state.status = "paused"
            state.paused_reason = "paused because a real user message preempted the automatic continuation"
            self.save_state(state, [_event("paused", turn=state.turns_used, summary=state.paused_reason)])
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:user-preempt:{turn_id}"}
        if interrupted:
            state.status = "paused"
            state.paused_reason = "paused because the user preempted the automatic continuation"
            self.save_state(state, [_event("paused", turn=state.turns_used, summary=state.paused_reason)])
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:preempt:{turn_id}"}
        if self.is_waiting(state):
            self.save_state(state)
            return {"action": "noop", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:wait:{turn_id}"}

        events = [
            _event(event_type, turn=state.turns_used + 1, summary=summary, data=data)
            for event_type, summary, data in extract_observation_events(final_response)
        ]
        if events:
            # Project in-memory together with already persisted events.
            projected = [
                GoalEvent(
                    ts=float(event.get("ts", 0.0) or 0.0),
                    type=str(event.get("type") or ""),
                    turn=int(event.get("turn", 0) or 0),
                    summary=str(event.get("summary") or ""),
                    data=dict(event.get("data") or {}),
                )
                for event in [*self.store.load_events(state.goal_run_id), *events]
            ]
            apply_events_to_state(state, projected, update_gates=update_supergoal_gates)
        update_supergoal_gates(state)

        state.turns_used += 1
        state.last_turn_at = time.time()
        judge_result = self.judge(
            state.goal,
            final_response,
            subgoals=state.subgoals or None,
            background_processes=background_processes or [],
            contract=state.contract if state.has_contract() else None,
        )
        verdict = str(judge_result[0] if len(judge_result) > 0 else "continue")
        reason = str(judge_result[1] if len(judge_result) > 1 else "")
        parse_failed = bool(judge_result[2] if len(judge_result) > 2 else False)
        state.last_verdict = verdict
        state.last_reason = reason
        state.consecutive_parse_failures = state.consecutive_parse_failures + 1 if parse_failed else 0

        critic_data = self.critic(state, final_response)
        if critic_data:
            apply_supergoal_critic(state, critic_data)
            state.consecutive_critic_failures = 0
        else:
            state.consecutive_critic_failures += 1
            update_supergoal_gates(state)

        terminal = _infer_terminal_blocker_status(" ".join([verdict, reason, final_response]))
        if terminal:
            state.status = "paused"
            state.paused_reason = reason or "terminal blocker"
            state.should_replan = True
            self.save_state(state, [*events, _event("blocked", turn=state.turns_used, summary=state.paused_reason, data={"control_status": terminal})])
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:blocked:{turn_id}", "state_version": state.turns_used}

        if verdict == "done":
            reconcile_done_evidence_gates(state, final_response, reason)
            update_supergoal_gates(state)
            first_blocking = first_blocking_failure(state)
            if first_blocking is None:
                state.status = "done"
                state.should_replan = False
                state.next_best_action = ""
                state.action_proposal = SupergoalActionProposal()
                self.save_state(state, [*events, _event("done", turn=state.turns_used, summary=reason, data={"reason": reason})])
                return {"action": "done", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:done", "state_version": state.turns_used}

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = "turn budget exhausted"
            self.save_state(state, [*events, _event("paused", turn=state.turns_used, summary=state.paused_reason)])
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:budget:{turn_id}", "state_version": state.turns_used}

        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        self.save_state(state, [*events, _event("turn_evaluated", turn=state.turns_used, summary=reason, data={"verdict": verdict})])
        return {
            "action": "continue",
            "continuation_prompt": prompt,
            "notice": f"Continuing /sgx: {reason}",
            "dedupe_key": f"{state.goal_run_id}:continue:{state.turns_used}:{turn_id}",
            "state_version": state.turns_used,
        }
