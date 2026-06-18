"""Deterministic gate specifications for Hermes /supergoal.

The concrete ``GoalGate`` dataclass lives in ``goals.py`` for backwards
compatible serialization, but gate selection policy lives here so the core
orchestration file does not own every mission-control concern.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Literal

GatePhase = Literal["intent", "research", "execution", "verification", "finalization", "safety"]
GateKind = Literal["run_acceptance", "quality_followup", "safety_hard", "domain_required"]


@dataclass(frozen=True)
class GateSpec:
    id: str
    description: str
    phase: GatePhase
    kind: GateKind
    blocking: bool
    verifier_id: str
    required_evidence: list[str] = field(default_factory=list)
    stale_after_turns: int | None = None

    @property
    def legacy_verifier(self) -> str:
        """Human-readable verifier retained for older status/debug surfaces."""
        if self.required_evidence:
            return f"{self.verifier_id}: {', '.join(self.required_evidence)}"
        return self.verifier_id


def _is_research_or_domain_goal(text: str) -> bool:
    return any(
        k in text
        for k in [
            "research", "survey", "literature", "paper", "papers", "sota", "benchmark",
            "architecture", "discover", "compare", "evaluate", "调研", "研究", "论文", "文献", "综述",
            "架构", "对比", "评估", "benchmark", "基准",
        ]
    )


def _is_strategy_goal(text: str) -> bool:
    return any(k in text for k in ["策略", "strategy", "trading", "交易", "edge", "hypothesis", "假设"])


def default_supergoal_gate_specs(goal: str = "") -> List[GateSpec]:
    """Return deterministic gate specs for a supergoal text.

    Gate ``kind`` is completion-critical: run-acceptance/domain/safety gates can
    veto a done judge, while quality-followup gates become followups instead of
    forcing the loop to continue.  G2 defaults to quality_followup so ordinary
    execution tasks can finish with verified artifacts even if no broad research
    ledger was required; explicitly research/strategy goals upgrade it to a
    blocking domain_required gate.
    """
    text = (goal or "").lower()
    g2_domain_required = _is_research_or_domain_goal(text) or _is_strategy_goal(text)
    g2_kind: GateKind = "domain_required" if g2_domain_required else "quality_followup"
    gates: List[GateSpec] = [
        GateSpec(
            id="G1",
            description="Intent contract captured: root intent, success criteria, anti-goals/constraints",
            phase="intent",
            kind="run_acceptance",
            blocking=True,
            verifier_id="intent_contract",
            required_evidence=["inferred_user_intent", "success_definition"],
        ),
        GateSpec(
            id="G2",
            description="Research ledger has sufficient tool-backed external provenance",
            phase="research",
            kind=g2_kind,
            blocking=g2_domain_required,
            verifier_id="tool_backed_research_provenance",
            required_evidence=["external_prior", "tool_call_id", "source_diversity"],
            stale_after_turns=6,
        ),
        GateSpec(
            id="G3",
            description="At least one concrete execution artifact is verified",
            phase="execution",
            kind="run_acceptance",
            blocking=True,
            verifier_id="verified_artifact",
            required_evidence=["artifact", "test/log/evidence"],
        ),
        GateSpec(
            id="G4",
            description="Final report maps evidence to success criteria or blocked/no-edge outcome",
            phase="finalization",
            kind="run_acceptance",
            blocking=True,
            verifier_id="final_evidence_mapping",
            required_evidence=["done verdict", "evidence mapping"],
        ),
    ]
    if _is_strategy_goal(text):
        gates.insert(2, GateSpec(
            id="SG-1",
            description="Hypothesis portfolio contains at least 3 strategy hypotheses",
            phase="research",
            kind="domain_required",
            blocking=True,
            verifier_id="hypothesis_portfolio_minimum",
            required_evidence=["len(hypothesis_portfolio) >= 3"],
        ))
        gates.insert(3, GateSpec(
            id="SG-2",
            description="Each active hypothesis has baseline, experiment, kill criteria, artifact, and verdict",
            phase="verification",
            kind="domain_required",
            blocking=True,
            verifier_id="hypothesis_verification_complete",
            required_evidence=["baseline", "experiment", "kill_criteria", "artifact", "verdict"],
        ))
        gates.insert(4, GateSpec(
            id="SG-3",
            description="If no hypothesis passes, a no-edge attribution report exists",
            phase="finalization",
            kind="domain_required",
            blocking=True,
            verifier_id="no_edge_attribution_if_needed",
            required_evidence=["passed hypothesis", "no_edge_report"],
        ))
        gates.insert(5, GateSpec(
            id="SG-4",
            description="Infrastructure work is allowed only when it proves dependency on a failed gate",
            phase="safety",
            kind="safety_hard",
            blocking=True,
            verifier_id="infra_dependency_proof",
            required_evidence=["non-infra action", "dependency proof"],
        ))
    return gates


def build_default_supergoal_gates(goal: str, gate_cls: Any) -> list:
    """Instantiate gate specs using the caller's GoalGate class."""
    return [
        gate_cls(
            spec.id,
            spec.description,
            status="pending" if spec.blocking else "followup",
            phase=spec.phase,
            kind=spec.kind,
            blocking=spec.blocking,
            verifier_id=spec.verifier_id,
            required_evidence=list(spec.required_evidence),
            stale_after_turns=spec.stale_after_turns,
            verifier=spec.legacy_verifier,
        )
        for spec in default_supergoal_gate_specs(goal)
    ]
