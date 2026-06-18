"""Explicit controller pipeline for /supergoal post-turn decisions.

The controller now exposes the intended state-machine phases explicitly:
observe → project → evaluate → reconcile → decide → render.

For this migration step, the heavyweight historical state-machine body is still
injected as ``legacy_decider`` and executed in the reconcile/decide boundary.
That preserves behavior while giving later refactors a stable place to move
logic phase-by-phase without touching CLI/Gateway callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from hermes_cli.supergoal.domain import (
    ActionProposal,
    ControllerDecision,
    DecisionDict,
    GateDecision,
    GateResult,
    PipelineSnapshot,
    TurnContext,
)
from hermes_cli.supergoal.evaluators import EvaluatorSuite

LegacyDecisionFn = Callable[..., DecisionDict]
ObserveEventsFn = Callable[[TurnContext], int]
ProjectStateFn = Callable[[TurnContext], bool]
PrepareEvaluationFn = Callable[[TurnContext], bool]
ReconcileDoneGatesFn = Callable[[TurnContext, str, str, Any, bool], Any]


@dataclass(frozen=True)
class SupergoalController:
    """Platform-neutral /supergoal controller boundary."""

    evaluators: EvaluatorSuite
    legacy_decider: LegacyDecisionFn
    observe_events: ObserveEventsFn | None = None
    project_state: ProjectStateFn | None = None
    prepare_evaluation: PrepareEvaluationFn | None = None
    reconcile_done_gates: ReconcileDoneGatesFn | None = None

    def decide_after_turn(self, ctx: TurnContext) -> ControllerDecision:
        snapshots: list[PipelineSnapshot] = []
        self._observe(ctx, snapshots)
        self._project(ctx, snapshots)
        evaluation = self._evaluate(ctx, snapshots)
        legacy = self._reconcile_and_decide(ctx, snapshots, evaluation)
        return self._render(ctx, legacy, snapshots)

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

    # 3. Evaluate --------------------------------------------------------
    def _evaluate(self, ctx: TurnContext, snapshots: list[PipelineSnapshot]) -> dict[str, Any]:
        state = ctx.state
        if getattr(state, "status", "") == "active":
            verdict, reason, parse_failed = self.evaluators.completion_judge.evaluate(
                getattr(state, "goal", ""),
                ctx.last_response,
                subgoals=getattr(state, "subgoals", []) or None,
            )
        else:
            verdict, reason, parse_failed = "inactive", "no active goal", False

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
            except Exception as exc:  # pragma: no cover - fail-closed path is reconciled by legacy guards
                critic_data = None
                critic_applied = False
                critic_error = str(exc)

        evaluation = {
            "verdict": verdict,
            "reason": reason,
            "parse_failed": parse_failed,
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

    # 4/5. Reconcile + Decide -------------------------------------------
    def _reconcile_and_decide(self, ctx: TurnContext, snapshots: list[PipelineSnapshot], evaluation: dict[str, Any]) -> DecisionDict:
        # Legacy decider currently performs stall/budget guards, persistence,
        # and continuation prompt rendering. Gate reconciliation is staged:
        # controller can precompute the done+gates result and legacy will apply
        # persistence/return-shaping without recomputing it.
        done_gate_result = None
        if self.reconcile_done_gates and evaluation.get("verdict") == "done":
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
        legacy = self.legacy_decider(
            ctx.last_response,
            user_initiated=ctx.user_initiated,
            supergoal_observed=bool(self.observe_events),
            supergoal_projected=bool(self.project_state),
            supergoal_evaluated=bool(self.prepare_evaluation),
            judge_result=(evaluation["verdict"], evaluation["reason"], evaluation["parse_failed"]),
            critic_data=evaluation.get("critic_data"),
            critic_applied=bool(evaluation.get("critic_applied")),
            critic_error=str(evaluation.get("critic_error") or ""),
            gate_ids_before_critic=evaluation.get("gate_ids_before_critic"),
            done_gate_result=done_gate_result,
        )
        gate_decision = self._gate_decision(ctx.state, legacy)
        legacy["gate_decision"] = gate_decision.to_dict()
        snapshots.append(
            PipelineSnapshot(
                phase="reconcile",
                summary=str(legacy.get("reason") or ""),
                data={
                    "legacy_status": legacy.get("status"),
                    "verdict": legacy.get("verdict"),
                    "should_continue": bool(legacy.get("should_continue", False)),
                    "done_gate_precomputed": done_gate_result is not None,
                    "gate_decision": gate_decision.to_dict(),
                },
            )
        )
        snapshots.append(
            PipelineSnapshot(
                phase="decide",
                summary=str(legacy.get("message") or legacy.get("reason") or ""),
                data={"continuation": bool(legacy.get("continuation_prompt"))},
            )
        )
        return legacy

    # 6. Render ----------------------------------------------------------
    def _render(
        self,
        ctx: TurnContext,
        legacy: DecisionDict,
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
            legacy,
            gate_results=gate_results,
            gate_decision=GateDecision.from_dict(legacy.get("gate_decision")),
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
