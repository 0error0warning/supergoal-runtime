"""Explicit controller pipeline for /supergoal post-turn decisions.

The controller now exposes the intended state-machine phases explicitly:
observe → project → evaluate → reconcile → decide → render.

For this migration step, side effects that still live at the GoalManager
boundary are injected as a named runtime-outcome adapter and executed in the
reconcile/decide boundary.  That preserves behavior while giving later refactors
a stable place to move logic phase-by-phase without touching CLI/Gateway callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from supergoal_runtime.domain import (
    ActionProposal,
    ControllerDecision,
    DecisionDict,
    GateDecision,
    GateResult,
    PipelineSnapshot,
    TurnContext,
    _infer_terminal_blocker_status,
)
from supergoal_runtime.evaluators import EvaluatorSuite

RuntimeOutcomeFn = Callable[..., DecisionDict]
ObserveEventsFn = Callable[[TurnContext], int]
ProjectStateFn = Callable[[TurnContext], bool]
PrepareEvaluationFn = Callable[[TurnContext], bool]
ReconcileDoneGatesFn = Callable[[TurnContext, str, str, Any, bool], Any]
ReplanIntervalFn = Callable[[], int]
PersistStateFn = Callable[[], None]
RecordEventFn = Callable[[str, str, dict[str, Any]], None]
ContinuationPromptFn = Callable[[], str | None]
ApplyDoneStateFn = Callable[[str], None]


@dataclass(frozen=True)
class SupergoalController:
    """Platform-neutral /supergoal controller boundary."""

    evaluators: EvaluatorSuite
    apply_runtime_outcome: RuntimeOutcomeFn
    observe_events: ObserveEventsFn | None = None
    project_state: ProjectStateFn | None = None
    prepare_evaluation: PrepareEvaluationFn | None = None
    reconcile_done_gates: ReconcileDoneGatesFn | None = None
    replan_interval: ReplanIntervalFn | None = None
    persist_state: PersistStateFn | None = None
    record_event: RecordEventFn | None = None
    build_continuation_prompt: ContinuationPromptFn | None = None
    apply_done_state: ApplyDoneStateFn | None = None

    def decide_after_turn(self, ctx: TurnContext) -> ControllerDecision:
        snapshots: list[PipelineSnapshot] = []
        self._observe(ctx, snapshots)
        self._project(ctx, snapshots)
        self._account_turn(ctx, snapshots)
        evaluation = self._evaluate(ctx, snapshots)
        runtime = self._reconcile_and_decide(ctx, snapshots, evaluation)
        return self._render(ctx, runtime, snapshots)

    # 1. Observe ---------------------------------------------------------
    def _observe(self, ctx: TurnContext, snapshots: list[PipelineSnapshot]) -> None:
        state = ctx.state
        event_count = self.observe_events(ctx) if self.observe_events else 0
        snapshots.append(
            PipelineSnapshot(
                phase="observe",
                summary="assistant turn completed; observation events recorded" if event_count else "assistant turn completed",
                data={
                    "session_id": ctx.session_id,
                    "goal_run_id": getattr(state, "goal_run_id", ""),
                    "response_chars": len(ctx.last_response or ""),
                    "turns_used_before": getattr(state, "turns_used", 0),
                    "observation_events": event_count,
                },
            )
        )

    # 2. Project ---------------------------------------------------------
    def _project(self, ctx: TurnContext, snapshots: list[PipelineSnapshot]) -> None:
        state = ctx.state
        changed = self.project_state(ctx) if self.project_state else False
        snapshots.append(
            PipelineSnapshot(
                phase="project",
                summary="board projected from persisted events" if self.project_state else "board projection available from persisted state/events",
                data={
                    "changed": changed,
                    "event_count": getattr(state, "event_count", 0),
                    "evidence_count": len(getattr(state, "evidence", []) or []),
                    "research_count": len(getattr(state, "research_findings", []) or []),
                    "action_history_count": len(getattr(state, "action_history", []) or []),
                },
            )
        )

    def _account_turn(self, ctx: TurnContext, snapshots: list[PipelineSnapshot]) -> None:
        """Count the completed turn before evaluation.

        Observation happens first and records events for ``turns_used + 1``;
        this method then advances the authoritative budget counter before judge,
        critic, periodic replan, and budget guards inspect it.
        """
        state = ctx.state
        if getattr(state, "status", "") != "active":
            return
        before = int(getattr(state, "turns_used", 0) or 0)
        state.turns_used = before + 1
        state.last_turn_at = time.time()
        if snapshots:
            data = dict(snapshots[-1].data or {})
            data["turns_used_after"] = state.turns_used
            snapshots[-1] = PipelineSnapshot(
                phase=snapshots[-1].phase,
                summary=snapshots[-1].summary,
                data=data,
            )

    # 3. Evaluate --------------------------------------------------------
    def _evaluate(self, ctx: TurnContext, snapshots: list[PipelineSnapshot]) -> dict[str, Any]:
        state = ctx.state
        if getattr(state, "status", "") == "active":
            judge_out = self.evaluators.completion_judge.evaluate(
                getattr(state, "goal", ""),
                ctx.last_response,
                subgoals=getattr(state, "subgoals", []) or None,
                background_processes=ctx.background_processes,
                contract=state.contract if getattr(state, "has_contract", lambda: False)() else None,
            )
            if len(judge_out) >= 4:
                verdict, reason, parse_failed, wait_directive = judge_out
            else:
                verdict, reason, parse_failed = judge_out
                wait_directive = None
        else:
            verdict, reason, parse_failed, wait_directive = "inactive", "no active goal", False, None

        if getattr(state, "status", "") == "active":
            setattr(state, "last_verdict", verdict)
            setattr(state, "last_reason", reason)
            self._track_judge_health(state, reason=reason, parse_failed=parse_failed)

        prepared = False
        critic_data: dict[str, Any] | None = None
        critic_applied = False
        critic_error = ""
        gate_ids_before_critic: set[str] = set()
        if getattr(state, "status", "") == "active" and getattr(state, "mode", "goal") == "supergoal":
            prepared = self.prepare_evaluation(ctx) if self.prepare_evaluation else False
            gate_ids_before_critic = self._passed_gate_ids(state)
            try:
                raw_critic_data = self.evaluators.strategic_critic.evaluate(state, ctx.last_response)
                if raw_critic_data:
                    critic_data = raw_critic_data
                    self.evaluators.strategic_critic.apply(state, critic_data)
                    critic_applied = True
            except Exception as exc:  # pragma: no cover - fail-closed path is reconciled by runtime guards
                critic_data = None
                critic_applied = False
                critic_error = str(exc)
            self._track_critic_health_and_replan(
                state,
                reason=reason,
                critic_applied=critic_applied,
            )

        evaluation = {
            "verdict": verdict,
            "reason": reason,
            "parse_failed": parse_failed,
            "wait_directive": wait_directive,
            "prepared": prepared,
            "critic_data": critic_data,
            "critic_applied": critic_applied,
            "critic_error": critic_error,
            "gate_ids_before_critic": gate_ids_before_critic,
        }
        snapshots.append(
            PipelineSnapshot(
                phase="evaluate",
                summary=f"completion judge verdict={verdict}; critic_applied={critic_applied}",
                data={
                    "completion_judge": type(self.evaluators.completion_judge).__name__,
                    "strategic_critic": type(self.evaluators.strategic_critic).__name__,
                    "gate_count": len(getattr(state, "gates", []) or []),
                    "verdict": verdict,
                    "parse_failed": parse_failed,
                    "prepared": prepared,
                    "critic_applied": critic_applied,
                    "critic_failed": bool(critic_error) or not bool(critic_data),
                },
            )
        )
        return evaluation

    @staticmethod
    def _track_judge_health(state: Any, *, reason: str, parse_failed: bool) -> None:
        """Maintain judge parse/API failure counters before runtime guards run."""
        if parse_failed:
            state.consecutive_parse_failures += 1
        else:
            state.consecutive_parse_failures = 0

        if reason and reason.startswith("judge error:"):
            state.consecutive_judge_api_failures += 1
        else:
            state.consecutive_judge_api_failures = 0

    def _track_critic_health_and_replan(self, state: Any, *, reason: str, critic_applied: bool) -> None:
        """Maintain critic health and periodic replan counters before runtime guards run."""
        if critic_applied:
            state.consecutive_critic_failures = 0
            interval = self.replan_interval() if self.replan_interval else 0
            if interval and getattr(state, "turns_used", 0) > 0 and state.turns_used % interval == 0:
                state.should_replan = True
                state.replan_count += 1
                if not getattr(state, "next_best_action", ""):
                    state.next_best_action = "Run a strategic replan before the next concrete step."
            return

        # If the strict judge API itself is down, don't also count the optional
        # critic path as a board failure; the separate judge-API guard owns that
        # fail-closed path.
        if not (reason and reason.startswith("judge error:")):
            state.consecutive_critic_failures += 1

    # 4/5. Reconcile + Decide -------------------------------------------
    def _reconcile_and_decide(self, ctx: TurnContext, snapshots: list[PipelineSnapshot], evaluation: dict[str, Any]) -> DecisionDict:
        # The GoalManager runtime adapter currently performs the remaining
        # side effects: turn accounting, failure counters, persistence, events,
        # budget guards, and continuation prompt rendering. Gate reconciliation
        # is staged: controller can precompute the done+gates result and the
        # adapter will apply persistence/return-shaping without recomputing it.
        done_gate_result = None
        terminal_blocker_status = ""
        if evaluation.get("verdict") == "done" and getattr(ctx.state, "mode", "goal") == "supergoal":
            terminal_blocker_status = _infer_terminal_blocker_status(
                " ".join([
                    str(evaluation.get("verdict") or ""),
                    str(evaluation.get("reason") or ""),
                    ctx.last_response or "",
                ])
            ) or ""
        if self.reconcile_done_gates and evaluation.get("verdict") == "done" and not terminal_blocker_status:
            # Legacy gate reconciliation used to run after last_verdict/last_reason
            # were mirrored onto state. Preserve that visible state contract before
            # moving the reconciliation call into the controller.
            setattr(ctx.state, "last_verdict", str(evaluation.get("verdict") or ""))
            setattr(ctx.state, "last_reason", str(evaluation.get("reason") or ""))
            done_gate_result = self.reconcile_done_gates(
                ctx,
                str(evaluation.get("reason") or ""),
                ctx.last_response,
                evaluation.get("gate_ids_before_critic"),
                bool(evaluation.get("critic_applied")),
            )
        runtime = self.apply_runtime_outcome(
            ctx.last_response,
            user_initiated=ctx.user_initiated,
            supergoal_observed=bool(self.observe_events),
            supergoal_projected=bool(self.project_state),
            supergoal_evaluated=bool(self.prepare_evaluation),
            turn_accounted=True,
            verdict_mirrored=True,
            judge_health_tracked=True,
            critic_health_tracked=True,
            periodic_replan_checked=True,
            continue_side_effects_applied=True,
            done_side_effects_applied=True,
            paused_side_effects_applied=True,
            judge_result=(
                evaluation["verdict"],
                evaluation["reason"],
                evaluation["parse_failed"],
                evaluation.get("wait_directive"),
            ),
            critic_data=evaluation.get("critic_data"),
            critic_applied=bool(evaluation.get("critic_applied")),
            critic_error=str(evaluation.get("critic_error") or ""),
            gate_ids_before_critic=evaluation.get("gate_ids_before_critic"),
            done_gate_result=done_gate_result,
        )
        if runtime.get("pause_state"):
            self._apply_pause_side_effects(ctx.state, runtime)
        elif runtime.get("verdict") == "done":
            self._apply_done_side_effects(ctx.state, runtime)
        elif runtime.get("status") == "active" and runtime.get("should_continue"):
            self._apply_continue_side_effects(ctx.state, runtime)
        gate_decision = self._gate_decision(ctx.state, runtime)
        runtime["gate_decision"] = gate_decision.to_dict()
        snapshots.append(
            PipelineSnapshot(
                phase="reconcile",
                summary=str(runtime.get("reason") or ""),
                data={
                    "legacy_status": runtime.get("status"),
                    "runtime_status": runtime.get("status"),
                    "verdict": runtime.get("verdict"),
                    "should_continue": bool(runtime.get("should_continue", False)),
                    "done_gate_precomputed": done_gate_result is not None,
                    "gate_decision": gate_decision.to_dict(),
                },
            )
        )
        snapshots.append(
            PipelineSnapshot(
                phase="decide",
                summary=str(runtime.get("message") or runtime.get("reason") or ""),
                data={"continuation": bool(runtime.get("continuation_prompt"))},
            )
        )
        return runtime

    def _apply_pause_side_effects(self, state: Any, runtime: DecisionDict) -> None:
        """Apply PAUSED mutations, persist state, and record the pause event."""
        pause_state = runtime.get("pause_state") if isinstance(runtime.get("pause_state"), dict) else {}
        state.status = "paused"
        paused_reason = str(pause_state.get("paused_reason") or runtime.get("reason") or "")
        if paused_reason:
            state.paused_reason = paused_reason
        if pause_state.get("should_replan"):
            state.should_replan = True
        next_best_action = str(pause_state.get("next_best_action") or "")
        if next_best_action and not getattr(state, "next_best_action", ""):
            state.next_best_action = next_best_action
        if self.persist_state:
            self.persist_state()
        if self.record_event:
            self.record_event(
                str(pause_state.get("event_type") or "paused"),
                str(pause_state.get("summary") or state.paused_reason or runtime.get("message") or ""),
                dict(pause_state.get("data") or {"reason": state.paused_reason}),
            )

    def _apply_done_side_effects(self, state: Any, runtime: DecisionDict) -> None:
        """Apply final DONE mutations, persist state, and record completion."""
        reason = str(runtime.get("reason") or "")
        if self.apply_done_state:
            self.apply_done_state(reason)
        if self.persist_state:
            self.persist_state()
        if self.record_event:
            self.record_event("done", reason, {"reason": reason})

    def _apply_continue_side_effects(self, state: Any, runtime: DecisionDict) -> None:
        """Persist active continue state and attach the next continuation prompt."""
        if self.persist_state:
            self.persist_state()
        reason = str(runtime.get("reason") or "")
        if self.record_event:
            self.record_event(
                "turn_evaluated",
                reason,
                {
                    "verdict": runtime.get("verdict"),
                    "reason": reason,
                    "turns_used": getattr(state, "turns_used", 0),
                    "current_step_id": getattr(state, "current_step_id", ""),
                },
            )
        runtime["continuation_prompt"] = self.build_continuation_prompt() if self.build_continuation_prompt else runtime.get("continuation_prompt")

    # 6. Render ----------------------------------------------------------
    def _render(
        self,
        ctx: TurnContext,
        runtime: DecisionDict,
        snapshots: list[PipelineSnapshot],
    ) -> ControllerDecision:
        state = ctx.state
        gate_results = self._gate_results(state)
        evidence_refs = self._evidence_refs(state)
        next_action = self._next_action(state)
        snapshots.append(
            PipelineSnapshot(
                phase="render",
                summary="controller decision rendered",
                data={
                    "gate_results": len(gate_results),
                    "evidence_refs": len(evidence_refs),
                    "has_next_action": next_action is not None,
                },
            )
        )
        return ControllerDecision.from_dict(
            runtime,
            gate_results=gate_results,
            gate_decision=GateDecision.from_dict(runtime.get("gate_decision")),
            evidence_refs=evidence_refs,
            next_action=next_action,
            snapshots=snapshots,
        )

    @staticmethod
    def _gate_results(state: Any) -> list[GateResult]:
        out: list[GateResult] = []
        for gate in getattr(state, "gates", []) or []:
            out.append(
                GateResult(
                    gate_id=str(getattr(gate, "id", "")),
                    status=str(getattr(gate, "status", "pending")),
                    blocking=bool(getattr(gate, "blocking", True)),
                    evidence_refs=[str(getattr(gate, "evidence", ""))] if getattr(gate, "evidence", "") else [],
                    missing=[str(x) for x in (getattr(gate, "missing", []) or [])],
                    reason=str(getattr(gate, "reason", "") or getattr(gate, "description", "")),
                    description=str(getattr(gate, "description", "")),
                    phase=str(getattr(gate, "phase", "verification")),
                    kind=str(getattr(gate, "kind", "run_acceptance")),
                )
            )
        return out

    def _gate_decision(self, state: Any, legacy: DecisionDict) -> GateDecision:
        reason = str(legacy.get("reason") or "")
        message = str(legacy.get("message") or "")
        control_status = str(legacy.get("control_status") or "")
        gate_results = self._gate_results(state)
        followup_gate_ids = [str(g) for g in (legacy.get("followup_gate_ids") or []) if str(g)]
        for gate in gate_results:
            if gate.status == "followup" and not gate.blocking and gate.gate_id not in followup_gate_ids:
                followup_gate_ids.append(gate.gate_id)
        first_blocking = next(
            (
                gate
                for gate in gate_results
                if gate.blocking and gate.status not in {"passed", "not_applicable", "followup"}
            ),
            None,
        )
        gate_vetoed = bool(
            first_blocking is not None
            and legacy.get("should_continue")
            and "blocking supergoal gate" in reason
        )
        return GateDecision(
            gate_vetoed=gate_vetoed,
            first_blocking_gate_id=first_blocking.gate_id if first_blocking else "",
            first_blocking_gate_description=first_blocking.description if first_blocking else "",
            followup_gate_ids=followup_gate_ids,
            done_with_followups=control_status == "done_with_followups" or bool(followup_gate_ids),
            stalled="stalled" in f"{reason} {message}".lower(),
        )

    @staticmethod
    def _evidence_refs(state: Any) -> list[str]:
        refs: list[str] = []
        for item in getattr(state, "evidence", []) or []:
            text = str(item).strip()
            if text:
                refs.append(text)
        for finding in getattr(state, "research_findings", []) or []:
            locator = str(getattr(finding, "locator", "") or "").strip()
            if locator:
                refs.append(locator)
        return refs[-24:]

    @staticmethod
    def _passed_gate_ids(state: Any) -> set[str]:
        return {
            str(getattr(gate, "id", ""))
            for gate in getattr(state, "gates", []) or []
            if str(getattr(gate, "status", "")) == "passed" and str(getattr(gate, "id", ""))
        }

    @staticmethod
    def _next_action(state: Any) -> ActionProposal | None:
        text = str(getattr(state, "next_best_action", "") or "").strip()
        if not text:
            return None
        proposal = getattr(state, "action_proposal", None)
        return ActionProposal(
            text=text,
            action_class=str(getattr(proposal, "action_class", "") or getattr(state, "current_action_class", "unknown") or "unknown"),
            target_gate_id=str(getattr(proposal, "target_gate_id", "") or ""),
            expected_evidence=list(getattr(proposal, "expected_evidence", []) or []),
            tools_needed=list(getattr(proposal, "tools_needed", []) or []),
            max_turn_budget=int(getattr(proposal, "max_turn_budget", 1) or 1),
            risk_level=str(getattr(proposal, "risk_level", "medium") or "medium"),
            why_this_gate_first=str(getattr(proposal, "why_this_gate_first", "") or ""),
            stop_if=list(getattr(proposal, "stop_if", []) or []),
            override_reason=str(getattr(proposal, "override_reason", "") or ""),
            why=str(getattr(state, "hard_gate_reason", "") or ""),
        )
