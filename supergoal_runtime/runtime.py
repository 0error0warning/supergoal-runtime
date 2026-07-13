"""Plugin-owned Supergoal runtime manager.

This module owns persistence orchestration for the standalone plugin. It does
not import Hermes internals; host-facing wrappers live in ``plugin.py`` and
``command.py``.
"""

from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from typing import Any, Callable

from .domain import DEFAULT_MAX_TURNS, GoalEvent, GoalState, SupergoalActionProposal, _infer_terminal_blocker_status
from .evaluators import apply_supergoal_critic
from .gates import first_blocking_failure, reconcile_done_evidence_gates, update_supergoal_gates
from .projection import apply_events_to_state, extract_observation_events
from .prompts import (
    CONTINUATION_PROMPT_PREFIX,
    LEGACY_CONTINUATION_PROMPT_PREFIX,
    START_PROMPT_PREFIX,
    build_continuation_prompt,
)
from .rendering import status_card, status_line
from .store import SupergoalStore

JudgeCallback = Callable[..., tuple[Any, ...]]
CriticCallback = Callable[[GoalState, str], dict[str, Any] | None]
EVALUATION_BUDGET_SECONDS = 20.0


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


def _continuation(
    *,
    session_id: str,
    prompt: str,
    kind: str,
) -> dict[str, Any]:
    return {
        "session_id": str(session_id),
        "token": f"sgx_{uuid.uuid4().hex}",
        "prompt": str(prompt),
        "kind": str(kind),
    }


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


def _has_explicit_completion_claim(text: str) -> bool:
    normalized = " ".join(str(text or "").casefold().split())
    if not normalized:
        return False
    negative = (
        "尚未完成",
        "任务未完成",
        "还未完成",
        "等待下一轮",
        "not complete",
        "not finished",
        "incomplete",
        "more work",
    )
    if any(marker in normalized for marker in negative):
        return False
    positive = (
        "全部完成",
        "整体完成",
        "整个任务完成",
        "已完成全部",
        "任务已完成",
        "goal is complete",
        "task is complete",
        "completed the entire task",
        "all requirements are satisfied",
        "all acceptance criteria are satisfied",
    )
    return any(marker in normalized for marker in positive)


class RuntimeManager:
    def __init__(
        self,
        *,
        store: SupergoalStore | None = None,
        judge: JudgeCallback | None = None,
        critic: CriticCallback | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
        evaluation_budget_seconds: float = EVALUATION_BUDGET_SECONDS,
    ) -> None:
        self.store = store or SupergoalStore()
        self.judge = judge or (lambda *_a, **_k: ("continue", "no judge callback configured", False))
        self.critic = critic or (lambda *_a, **_k: None)
        self.default_max_turns = int(max_turns or DEFAULT_MAX_TURNS)
        self.evaluation_budget_seconds = min(
            EVALUATION_BUDGET_SECONDS,
            max(0.01, float(evaluation_budget_seconds)),
        )

    def start(self, session_id: str, goal: str, *, max_turns: int | None = None) -> GoalState:
        state, _envelope = self._start(
            session_id,
            goal,
            max_turns=max_turns,
            durable_continuation=False,
        )
        return state

    def start_for_command(
        self,
        session_id: str,
        goal: str,
        *,
        max_turns: int | None = None,
    ) -> tuple[GoalState, dict[str, Any]]:
        state, envelope = self._start(
            session_id,
            goal,
            max_turns=max_turns,
            durable_continuation=True,
        )
        assert envelope is not None
        return state, envelope

    def _start(
        self,
        session_id: str,
        goal: str,
        *,
        max_turns: int | None,
        durable_continuation: bool,
    ) -> tuple[GoalState, dict[str, Any] | None]:
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
        envelope = None
        continuation = None
        if durable_continuation:
            continuation = _continuation(
                session_id=session_id,
                prompt=f"{START_PROMPT_PREFIX}\nGoal: {state.goal}",
                kind="start",
            )
            envelope = {**continuation, "state_version": 0}
        self.store.import_run_bundle(
            goal_run_id,
            state_to_record(state),
            bindings=[(session_id, "sgx_start")],
            events=[(_event("started", summary="Supergoal started", data={"session_id": session_id}), "start", 0)],
            continuation=continuation,
        )
        return state, envelope

    def _load_state_snapshot(
        self, session_id: str
    ) -> tuple[GoalState | None, int]:
        _goal_run_id, record, revision = self.store.load_bound_run_snapshot(session_id)
        return state_from_record(record), revision

    def load_state_for_session(self, session_id: str) -> GoalState | None:
        state, _revision = self._load_state_snapshot(session_id)
        return state

    def save_state(self, state: GoalState, events: list[dict[str, Any]] | None = None) -> None:
        self.store.save_run_with_events(state.goal_run_id, state_to_record(state), events or [])

    def _commit_state(
        self,
        state: GoalState,
        events: list[dict[str, Any]],
        *,
        expected_revision: int,
        require_active: bool = False,
        continuation: dict[str, Any] | None = None,
        cancel_pending: bool = False,
        settle_claimed: bool = False,
        cancel_reason: str = "run state superseded",
    ) -> tuple[int, dict[str, Any] | None] | None:
        return self.store.save_run_with_events_cas(
            state.goal_run_id,
            state_to_record(state),
            events,
            expected_revision=expected_revision,
            require_status="active" if require_active else None,
            continuation=continuation,
            cancel_pending=cancel_pending,
            settle_claimed=settle_claimed,
            cancel_reason=cancel_reason,
        )

    def status_text(self, session_id: str) -> str:
        return status_card(self.load_state_for_session(session_id)).to_text()

    def pause(self, session_id: str) -> str:
        state, revision = self._load_state_snapshot(session_id)
        if state is None:
            return status_line(None)
        if state.status == "paused":
            self.store.cancel_continuations(
                goal_run_id=state.goal_run_id,
                reason="paused by user",
            )
            return status_line(state)
        if state.status in {"done", "cleared"}:
            return status_line(state)
        state.status = "paused"
        state.paused_reason = "paused by user"
        committed = self._commit_state(
            state,
            [_event("paused", turn=state.turns_used, summary="paused by user")],
            expected_revision=revision,
            require_active=True,
            cancel_pending=True,
            cancel_reason="paused by user",
        )
        return status_line(state if committed else self.load_state_for_session(session_id))

    def resume(self, session_id: str) -> tuple[str, str | None]:
        text, prompt, _envelope = self._resume(
            session_id,
            durable_continuation=False,
        )
        return text, prompt

    def resume_for_command(
        self, session_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        text, _prompt, envelope = self._resume(
            session_id,
            durable_continuation=True,
        )
        return text, envelope

    def _resume(
        self,
        session_id: str,
        *,
        durable_continuation: bool,
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        state, revision = self._load_state_snapshot(session_id)
        if state is None:
            return status_line(None), None, None
        if state.status in {"done", "cleared"}:
            return status_line(state), None, None
        state.status = "active"
        state.paused_reason = None
        update_supergoal_gates(state)
        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        continuation = (
            _continuation(
                session_id=session_id,
                prompt=prompt,
                kind="resume",
            )
            if durable_continuation
            else None
        )
        committed = self._commit_state(
            state,
            [_event("resumed", turn=state.turns_used, summary="resumed by user")],
            expected_revision=revision,
            continuation=continuation,
            cancel_pending=durable_continuation,
            cancel_reason="resumed by user",
        )
        if not committed:
            latest = self.load_state_for_session(session_id)
            return status_line(latest), None, None
        return status_line(state), prompt, committed[1]

    def clear(self, session_id: str) -> str:
        state, revision = self._load_state_snapshot(session_id)
        if state is None:
            return "No active /sgx supergoal for this session."
        if state.status == "cleared":
            self.store.cancel_continuations(
                goal_run_id=state.goal_run_id,
                reason="cleared by user",
            )
            return status_line(state)
        state.status = "cleared"
        state.paused_reason = "cleared by user"
        committed = self._commit_state(
            state,
            [_event("cleared", turn=state.turns_used, summary="cleared by user")],
            expected_revision=revision,
            cancel_pending=True,
            cancel_reason="cleared by user",
        )
        return status_line(state if committed else self.load_state_for_session(session_id))

    def wait(self, session_id: str, target: str) -> str:
        state, revision = self._load_state_snapshot(session_id)
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
        committed = self._commit_state(
            state,
            [_event("wait_started", turn=state.turns_used, summary=state.waiting_reason)],
            expected_revision=revision,
            require_active=True,
            cancel_pending=True,
            cancel_reason="wait barrier started",
        )
        return status_line(state if committed else self.load_state_for_session(session_id))

    def unwait(self, session_id: str) -> str:
        state, revision = self._load_state_snapshot(session_id)
        if state is None:
            return status_line(None)
        state.waiting_on_pid = None
        state.waiting_on_session = None
        state.waiting_until = 0.0
        state.waiting_reason = None
        committed = self._commit_state(
            state,
            [_event("wait_cleared", turn=state.turns_used, summary="wait cleared")],
            expected_revision=revision,
            require_active=True,
            cancel_pending=True,
            cancel_reason="wait barrier cleared",
        )
        return status_line(state if committed else self.load_state_for_session(session_id))

    def replan(self, session_id: str) -> tuple[str, str | None]:
        text, prompt, _envelope = self._replan(
            session_id,
            durable_continuation=False,
        )
        return text, prompt

    def replan_for_command(
        self, session_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        text, _prompt, envelope = self._replan(
            session_id,
            durable_continuation=True,
        )
        return text, envelope

    def _replan(
        self,
        session_id: str,
        *,
        durable_continuation: bool,
    ) -> tuple[str, str | None, dict[str, Any] | None]:
        state, revision = self._load_state_snapshot(session_id)
        if state is None:
            return status_line(None), None, None
        if state.status != "active":
            return status_line(state), None, None
        state.should_replan = True
        state.replan_count += 1
        state.next_best_action = state.next_best_action or "Replan against the first failed blocking gate."
        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        continuation = (
            _continuation(
                session_id=session_id,
                prompt=prompt,
                kind="replan",
            )
            if durable_continuation
            else None
        )
        committed = self._commit_state(
            state,
            [_event("replan_requested", turn=state.turns_used, summary="replan requested")],
            expected_revision=revision,
            require_active=True,
            continuation=continuation,
            cancel_pending=durable_continuation,
            cancel_reason="replan requested",
        )
        if not committed:
            return status_line(self.load_state_for_session(session_id)), None, None
        return status_line(state), prompt, committed[1]

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

    def _run_evaluators(
        self,
        state: GoalState,
        final_response: str,
        *,
        background_processes: list[dict[str, Any]],
    ) -> tuple[tuple[Any, ...], dict[str, Any] | None, list[dict[str, Any]]]:
        """Run judge and critic concurrently under one bounded budget.

        Each callback receives its own state snapshot. Timed-out worker threads
        can finish later, but they cannot mutate or persist the authoritative
        state; the caller alone performs the subsequent CAS commit.
        """

        judge_state = state_from_record(state_to_record(state)) or state
        critic_state = state_from_record(state_to_record(state)) or state

        def call_judge() -> tuple[Any, ...]:
            result = self.judge(
                judge_state.goal,
                final_response,
                subgoals=judge_state.subgoals or None,
                background_processes=background_processes,
                contract=judge_state.contract if judge_state.has_contract() else None,
                state=judge_state,
            )
            return tuple(result) if isinstance(result, (tuple, list)) else ()

        def call_critic() -> dict[str, Any] | None:
            result = self.critic(critic_state, final_response)
            return result if isinstance(result, dict) else None

        executor = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="supergoal-evaluator",
        )
        judge_future: Future[tuple[Any, ...]] = executor.submit(call_judge)
        critic_future: Future[dict[str, Any] | None] = executor.submit(call_critic)
        completed, _pending = wait(
            (judge_future, critic_future),
            timeout=self.evaluation_budget_seconds,
        )
        diagnostics: list[dict[str, Any]] = []
        judge_result: tuple[Any, ...] = (
            "continue",
            "judge evaluation timed out",
            True,
        )
        critic_data: dict[str, Any] | None = None
        if judge_future in completed:
            try:
                judge_result = judge_future.result()
            except Exception as exc:
                judge_result = (
                    "continue",
                    f"judge evaluation failed: {type(exc).__name__}",
                    True,
                )
                diagnostics.append(
                    _event(
                        "judge_failed",
                        turn=state.turns_used,
                        summary=type(exc).__name__,
                    )
                )
        else:
            diagnostics.append(
                _event(
                    "judge_timed_out",
                    turn=state.turns_used,
                    summary=f"shared evaluator budget {self.evaluation_budget_seconds:.2f}s",
                )
            )
        if critic_future in completed:
            try:
                critic_data = critic_future.result()
            except Exception as exc:
                diagnostics.append(
                    _event(
                        "critic_failed",
                        turn=state.turns_used,
                        summary=type(exc).__name__,
                    )
                )
        else:
            diagnostics.append(
                _event(
                    "critic_timed_out",
                    turn=state.turns_used,
                    summary=f"shared evaluator budget {self.evaluation_budget_seconds:.2f}s",
                )
            )
        executor.shutdown(wait=False, cancel_futures=True)
        return judge_result, critic_data, diagnostics

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
        state, revision = self._load_state_snapshot(session_id)
        if state is None or state.status != "active":
            return None
        message = str(user_message or "").lstrip()
        synthetic_turn = (
            message.startswith(START_PROMPT_PREFIX)
            or message.startswith(CONTINUATION_PROMPT_PREFIX)
            or message.startswith(LEGACY_CONTINUATION_PROMPT_PREFIX)
        )
        if message and not synthetic_turn:
            state.status = "paused"
            state.paused_reason = "paused because a real user message preempted the automatic continuation"
            committed = self._commit_state(
                state,
                [_event("paused", turn=state.turns_used, summary=state.paused_reason)],
                expected_revision=revision,
                require_active=True,
                cancel_pending=True,
                cancel_reason="real user message preempted continuation",
            )
            if not committed:
                return None
            return {
                "action": "pause",
                "notice": status_line(state),
                "dedupe_key": f"{state.goal_run_id}:user-preempt:{turn_id}",
                "state_version": committed[0],
            }
        if interrupted:
            state.status = "paused"
            state.paused_reason = "paused because the user preempted the automatic continuation"
            committed = self._commit_state(
                state,
                [_event("paused", turn=state.turns_used, summary=state.paused_reason)],
                expected_revision=revision,
                require_active=True,
                cancel_pending=True,
                cancel_reason="turn interrupted",
            )
            if not committed:
                return None
            return {
                "action": "pause",
                "notice": status_line(state),
                "dedupe_key": f"{state.goal_run_id}:preempt:{turn_id}",
                "state_version": committed[0],
            }
        if self.is_waiting(state):
            committed = self._commit_state(
                state,
                [],
                expected_revision=revision,
                require_active=True,
                settle_claimed=True,
                cancel_reason="turn parked at wait barrier",
            )
            if not committed:
                return None
            return {"action": "noop", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:wait:{turn_id}"}

        events = [
            _event(event_type, turn=state.turns_used + 1, summary=summary, data=data)
            for event_type, summary, data in extract_observation_events(final_response)
        ]
        # One ledger read serves both projection and completion verification.
        # Keeping database work outside the post-evaluator commit path leaves
        # enough headroom under the host's 30-second controller deadline.
        persisted_events = self.store.load_events(state.goal_run_id)
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
                for event in [*persisted_events, *events]
            ]
            apply_events_to_state(state, projected, update_gates=update_supergoal_gates)
        update_supergoal_gates(state)

        state.turns_used += 1
        state.last_turn_at = time.time()
        judge_result, critic_data, evaluator_events = self._run_evaluators(
            state,
            final_response,
            background_processes=background_processes or [],
        )
        verdict = str(judge_result[0] if len(judge_result) > 0 else "continue")
        reason = str(judge_result[1] if len(judge_result) > 1 else "")
        parse_failed = bool(judge_result[2] if len(judge_result) > 2 else False)
        state.last_verdict = verdict
        state.last_reason = reason
        state.consecutive_parse_failures = state.consecutive_parse_failures + 1 if parse_failed else 0

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
            committed = self._commit_state(
                state,
                [*events, *evaluator_events, _event("blocked", turn=state.turns_used, summary=state.paused_reason, data={"control_status": terminal})],
                expected_revision=revision,
                require_active=True,
                settle_claimed=True,
                cancel_reason="terminal blocker",
            )
            if not committed:
                return None
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:blocked:{turn_id}", "state_version": committed[0]}

        completion_claim = _has_explicit_completion_claim(final_response)
        verification_seen = any(
            event.get("type") == "verification_observed"
            for event in [*persisted_events, *events]
        )
        if verdict == "done" or (completion_claim and verification_seen):
            reconcile_done_evidence_gates(state, final_response, reason)
            update_supergoal_gates(state)
            first_blocking = first_blocking_failure(state)
            if first_blocking is None:
                completion_reason = reason if verdict == "done" else "explicit completion confirmed by tool-backed gates"
                state.status = "done"
                state.last_verdict = "done"
                state.last_reason = completion_reason
                state.should_replan = False
                state.next_best_action = ""
                state.action_proposal = SupergoalActionProposal()
                committed = self._commit_state(
                    state,
                    [
                        *events,
                        *evaluator_events,
                        _event(
                            "done",
                            turn=state.turns_used,
                            summary=completion_reason,
                            data={"reason": completion_reason, "local_completion": verdict != "done"},
                        ),
                    ],
                    expected_revision=revision,
                    require_active=True,
                    settle_claimed=True,
                    cancel_reason="supergoal completed",
                )
                if not committed:
                    return None
                return {"action": "done", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:done", "state_version": committed[0]}

        if state.turns_used >= state.max_turns:
            state.status = "paused"
            state.paused_reason = "turn budget exhausted"
            committed = self._commit_state(
                state,
                [*events, *evaluator_events, _event("paused", turn=state.turns_used, summary=state.paused_reason)],
                expected_revision=revision,
                require_active=True,
                settle_claimed=True,
                cancel_reason="turn budget exhausted",
            )
            if not committed:
                return None
            return {"action": "pause", "notice": status_line(state), "dedupe_key": f"{state.goal_run_id}:budget:{turn_id}", "state_version": committed[0]}

        prompt = build_continuation_prompt(state, board=state.render_supergoal_board())
        continuation = _continuation(
            session_id=session_id,
            prompt=prompt,
            kind="after_turn",
        )
        committed = self._commit_state(
            state,
            [*events, *evaluator_events, _event("turn_evaluated", turn=state.turns_used, summary=reason, data={"verdict": verdict})],
            expected_revision=revision,
            require_active=True,
            continuation=continuation,
            settle_claimed=True,
            cancel_reason="turn evaluated",
        )
        if not committed or committed[1] is None:
            return None
        envelope = committed[1]
        return {
            "action": "continue",
            "continuation_prompt": prompt,
            "notice": f"Continuing /sgx: {reason}",
            "dedupe_key": f"{state.goal_run_id}:continue:{state.turns_used}:{turn_id}",
            "state_version": committed[0],
            "continuation_token": envelope["token"],
        }
