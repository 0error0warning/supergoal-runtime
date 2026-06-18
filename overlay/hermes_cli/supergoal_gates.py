"""Deterministic gate specifications for Hermes /supergoal.

The concrete ``GoalGate`` dataclass lives in ``goals.py`` for backwards
compatible serialization, but gate selection policy lives here so the core
orchestration file does not own every mission-control concern.
"""

from __future__ import annotations

from typing import Any, List, Tuple

GateSpec = Tuple[str, str, str]


def default_supergoal_gate_specs(goal: str = "") -> List[GateSpec]:
    """Return deterministic gate specs for a supergoal text."""
    text = (goal or "").lower()
    gates: List[GateSpec] = [
        (
            "G1",
            "Intent contract captured: root intent, success criteria, anti-goals/constraints",
            "state.inferred_user_intent and state.success_definition",
        ),
        (
            "G2",
            "Research ledger has sufficient tool-backed external provenance",
            "paper+github or 3 external tool-backed source types",
        ),
        (
            "G3",
            "At least one concrete execution artifact is verified",
            "evidence/artifact/log/test recorded",
        ),
        (
            "G4",
            "Final report maps evidence to success criteria or blocked/no-edge outcome",
            "done verdict or no_edge_report",
        ),
    ]
    if any(k in text for k in ["策略", "strategy", "trading", "交易", "edge", "hypothesis", "假设"]):
        gates.insert(2, (
            "SG-1",
            "Hypothesis portfolio contains at least 3 strategy hypotheses",
            "len(hypothesis_portfolio) >= 3",
        ))
        gates.insert(3, (
            "SG-2",
            "Each active hypothesis has baseline, experiment, kill criteria, artifact, and verdict",
            "portfolio completeness + verdicts",
        ))
        gates.insert(4, (
            "SG-3",
            "If no hypothesis passes, a no-edge attribution report exists",
            "no_edge_report when all tested hypotheses fail/kill",
        ))
        gates.insert(5, (
            "SG-4",
            "Infrastructure work is allowed only when it proves dependency on a failed gate",
            "infra dependency proof",
        ))
    return gates


def build_default_supergoal_gates(goal: str, gate_cls: Any) -> list:
    """Instantiate gate specs using the caller's GoalGate class."""
    return [gate_cls(gid, description, verifier=verifier) for gid, description, verifier in default_supergoal_gate_specs(goal)]
