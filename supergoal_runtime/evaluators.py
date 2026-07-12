"""Evaluator adapters for the /supergoal runtime.

The concrete judge/critic functions remain in ``hermes_cli.goals`` for this
migration step so existing tests can still monkeypatch them.  These adapters
make the controller depend on explicit evaluator objects instead of reaching
through GoalManager for everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .domain import (
    HypothesisRecord,
    ResearchFinding,
    SupergoalActionProposal,
    _clean_string_list,
    _coerce_float,
)
from .gates import apply_inertia_guard, first_blocking_failure, update_supergoal_gates
from .projection import merge_compact_list, merge_research_findings, research_sufficiency_from_findings

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


def merge_hypothesis_portfolio(
    existing: list[HypothesisRecord],
    new_items: Any,
    *,
    max_items: int = 16,
) -> list[HypothesisRecord]:
    merged = list(existing or [])
    seen = {(h.id.lower(), h.claim.lower()) for h in merged}
    if isinstance(new_items, (dict, str)):
        new_items = [new_items]
    if not isinstance(new_items, list):
        return merged[-max_items:]
    next_num = len(merged) + 1
    for item in new_items:
        hypothesis = HypothesisRecord.from_dict(item)
        if hypothesis is None:
            continue
        if hypothesis.id == "H?":
            hypothesis.id = f"H{next_num}"
            next_num += 1
        key = (hypothesis.id.lower(), hypothesis.claim.lower())
        claim_seen = {h.claim.lower() for h in merged}
        if key in seen or hypothesis.claim.lower() in claim_seen:
            continue
        seen.add(key)
        merged.append(hypothesis)
    return merged[-max_items:]


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
    if first_gate is not None and not proposal.target_gate_id:
        proposal.target_gate_id = first_gate.id
        proposal.why_this_gate_first = proposal.why_this_gate_first or "first failed blocking gate"
        if not proposal.expected_evidence:
            proposal.expected_evidence = [first_gate.description]
    return proposal


def apply_supergoal_critic(state: Any, data: Optional[dict[str, Any]]) -> None:
    """Merge critic output into the persistent Supergoal state board."""
    if not state or not data:
        return

    intent = str(data.get("inferred_user_intent") or "").strip()
    if intent:
        state.inferred_user_intent = " ".join(intent.split())[:300]
    success = str(data.get("success_definition") or "").strip()
    if success:
        state.success_definition = " ".join(success.split())[:300]
    state.first_principles_model = merge_compact_list(
        getattr(state, "first_principles_model", []) or [],
        data.get("first_principles_model"),
        max_items=16,
    )
    state.existing_solution_scan = merge_compact_list(
        getattr(state, "existing_solution_scan", []) or [],
        data.get("existing_solution_scan"),
        max_items=16,
    )
    reuse_decision = str(data.get("build_vs_reuse_decision") or "").strip()
    if reuse_decision:
        state.build_vs_reuse_decision = " ".join(reuse_decision.split())[:300]
    literalism = str(data.get("literalism_risk") or "").strip().lower()
    if literalism in {"low", "medium", "high"}:
        state.literalism_risk = literalism
    research = str(data.get("research_sufficiency") or "").strip().lower()
    if research not in {"sufficient", "thin", "missing"}:
        research = ""
    current_findings = list(getattr(state, "research_findings", []) or [])
    if current_findings and not isinstance(current_findings[0], ResearchFinding):
        current_findings = [
            finding for item in current_findings if (finding := ResearchFinding.from_dict(item)) is not None
        ]
    state.research_findings = merge_research_findings(current_findings, data.get("research_findings"))
    state.research_sufficiency = research_sufficiency_from_findings(state, research)
    state.hypothesis_portfolio = merge_hypothesis_portfolio(
        getattr(state, "hypothesis_portfolio", []) or [],
        data.get("hypothesis_portfolio") or data.get("new_hypotheses"),
    )
    no_edge = str(data.get("no_edge_report") or "").strip()
    if no_edge:
        state.no_edge_report = " ".join(no_edge.split())[:500]

    update_supergoal_gates(state)
    proposal = proposal_from_critic_data(state, data)
    state.action_proposal = proposal
    state.current_action_class = proposal.action_class or "unknown"

    progress = str(data.get("progress") or "").strip().lower()
    if progress in {"real", "weak", "none", "regressed"}:
        state.progress = progress
    health = str(data.get("strategy_health") or "").strip().lower()
    if health in {"good", "stuck", "drifting", "repeating", "premature", "blocked"}:
        state.strategy_health = health
    if "root_cause_confidence" in data:
        state.root_cause_confidence = _coerce_float(
            data.get("root_cause_confidence"), getattr(state, "root_cause_confidence", 0.0)
        )
    quality_replan = getattr(state, "literalism_risk", "") == "high" or getattr(state, "research_sufficiency", "") in {"thin", "missing"}
    state.should_replan = bool(data.get("should_replan", False)) or quality_replan or getattr(state, "strategy_health", "") in {
        "stuck", "drifting", "repeating", "premature", "blocked"
    } or getattr(state, "progress", "") in {"none", "regressed"}
    if state.should_replan:
        state.replan_count = int(getattr(state, "replan_count", 0) or 0) + 1
    nba = proposal.text or str(data.get("next_best_action") or "").strip()
    if nba:
        state.next_best_action = " ".join(nba.split())[:300]

    state.milestones = merge_compact_list(getattr(state, "milestones", []) or [], data.get("new_milestones"))
    state.hypotheses = merge_compact_list(getattr(state, "hypotheses", []) or [], data.get("new_hypotheses"))
    state.evidence = merge_compact_list(getattr(state, "evidence", []) or [], data.get("new_evidence"))
    state.attempted_solutions = merge_compact_list(
        getattr(state, "attempted_solutions", []) or [], data.get("new_attempted_solutions")
    )
    state.blockers = merge_compact_list(getattr(state, "blockers", []) or [], data.get("new_blockers"))
    missing = _clean_string_list(data.get("missing_evidence"), limit=8)
    if missing:
        state.risks = merge_compact_list(
            getattr(state, "risks", []) or [],
            [f"missing evidence: {item}" for item in missing],
            max_items=20,
        )
    state.risks = merge_compact_list(getattr(state, "risks", []) or [], data.get("new_risks"))
    if getattr(state, "literalism_risk", "") == "high":
        state.risks = merge_compact_list(
            getattr(state, "risks", []) or [],
            ["literalism risk: agent may be following the written task without satisfying root intent"],
            max_items=20,
        )
    if getattr(state, "research_sufficiency", "") in {"thin", "missing"}:
        state.risks = merge_compact_list(
            getattr(state, "risks", []) or [],
            [f"tool-backed research ledger is {state.research_sufficiency}"],
            max_items=20,
        )
    update_supergoal_gates(state)
    apply_inertia_guard(state)
