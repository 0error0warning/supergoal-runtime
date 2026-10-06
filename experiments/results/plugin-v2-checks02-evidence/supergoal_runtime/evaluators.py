"""Evaluator adapters and advisory progress updates for the plugin runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .domain import (
    SupergoalActionProposal,
    _clean_string_list,
)
from .gates import apply_inertia_guard, first_blocking_failure, update_supergoal_gates
from .projection import merge_compact_list

JudgeResult = Tuple[str, str, bool]
JudgeFn = Callable[[str, str], JudgeResult]
CriticFn = Callable[[Any, str], Optional[Dict[str, Any]]]
CriticApplyFn = Callable[[Any, Optional[Dict[str, Any]]], None]


@dataclass(frozen=True)
class CompletionJudge:
    """Adapter around the completion judge callable."""

    judge_fn: Callable[..., JudgeResult]

    def evaluate(
        self,
        goal: str,
        last_response: str,
        *,
        subgoals: Optional[List[str]] = None,
        background_processes: Optional[List[dict]] = None,
        contract: Any = None,
    ) -> JudgeResult:
        return self.judge_fn(
            goal,
            last_response,
            subgoals=subgoals or None,
            background_processes=background_processes,
            contract=contract,
        )


@dataclass(frozen=True)
class StrategicCritic:
    """Adapter around the strategic critic + merge callables."""

    critic_fn: Callable[[Any, str], Optional[Dict[str, Any]]]
    apply_fn: Callable[[Any, Optional[Dict[str, Any]]], None]

    def evaluate(self, state: Any, last_response: str) -> Optional[Dict[str, Any]]:
        return self.critic_fn(state, last_response)

    def apply(self, state: Any, data: Optional[Dict[str, Any]]) -> None:
        self.apply_fn(state, data)


@dataclass(frozen=True)
class EvaluatorSuite:
    completion_judge: CompletionJudge
    strategic_critic: StrategicCritic


def fallback_action_proposal_for_state(state: Any, text: str = "") -> SupergoalActionProposal:
    first_gate = first_blocking_failure(state)
    action_text = text or getattr(state, "next_best_action", "") or (
        f"Satisfy gate {first_gate.id}: {first_gate.description}" if first_gate else ""
    )
    from .projection import classify_action_text

    return SupergoalActionProposal(
        action_class=classify_action_text(action_text),
        target_gate_id=getattr(first_gate, "id", "") if first_gate else "",
        expected_evidence=[getattr(first_gate, "description", "")] if first_gate else [],
        tools_needed=[],
        max_turn_budget=1,
        risk_level="medium",
        why_this_gate_first="first failed blocking gate" if first_gate else "fallback action proposal",
        stop_if=["evidence does not increase after this turn"],
        text=" ".join(str(action_text).split())[:300],
    )


def proposal_from_critic_data(state: Any, data: dict[str, Any]) -> SupergoalActionProposal:
    raw = data.get("action_proposal")
    proposal = SupergoalActionProposal.from_dict(raw) if isinstance(raw, dict) else SupergoalActionProposal()
    nba = str(data.get("next_best_action") or "").strip()
    current_action = str(data.get("current_action_class") or "").strip().lower()
    allowed = {"research", "hypothesis_generation", "experiment_execution", "validation", "infra_engineering", "reporting", "safety", "unknown"}
    if proposal.is_empty():
        proposal = fallback_action_proposal_for_state(state, nba)
    if current_action in allowed and current_action != "unknown" and proposal.action_class == "unknown":
        proposal.action_class = current_action
    if nba and not proposal.text:
        proposal.text = " ".join(nba.split())[:300]
    first_gate = first_blocking_failure(state)
    active_ids = {gate.id for gate in (getattr(state, "gates", []) or [])}
    if first_gate is not None and proposal.target_gate_id not in active_ids:
        proposal.target_gate_id = first_gate.id
        proposal.why_this_gate_first = proposal.why_this_gate_first or "first failed blocking gate"
        if not proposal.expected_evidence:
            proposal.expected_evidence = [first_gate.description]
    return proposal


def apply_supergoal_critic(state: Any, data: Optional[dict[str, Any]]) -> None:
    """Merge advisory progress without changing the authoritative task contract."""
    if not state or not data:
        return
    update_supergoal_gates(state)
    proposal = proposal_from_critic_data(state, data)
    state.action_proposal = proposal
    state.current_action_class = proposal.action_class or "unknown"
    progress = str(data.get("progress") or "").strip().lower()
    if progress in {"real", "weak", "none", "regressed"}:
        state.progress = progress
    health = str(data.get("plan_health") or "").strip().lower()
    if health in {"good", "stuck", "drifting", "repeating", "blocked"}:
        state.plan_health = health
    state.should_replan = bool(data.get("should_replan", False)) or state.plan_health in {
        "stuck", "drifting", "repeating", "blocked"
    } or state.progress in {"none", "regressed"}
    if state.should_replan:
        state.replan_count += 1
    if proposal.text:
        state.next_best_action = proposal.text
    for name, key in (("milestones", "new_milestones"), ("attempted_solutions", "new_attempted_solutions"),
                      ("blockers", "new_blockers"), ("risks", "new_risks")):
        setattr(state, name, merge_compact_list(getattr(state, name, []) or [], data.get(key)))
    missing = _clean_string_list(data.get("missing_evidence"), limit=8)
    if missing:
        state.risks = merge_compact_list(state.risks, [f"missing evidence: {item}" for item in missing])
    apply_inertia_guard(state)
