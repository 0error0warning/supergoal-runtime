"""Retire old built-in domain policies without erasing their audit history."""

from __future__ import annotations

from typing import Any

from ..domain import SupergoalActionProposal

LEGACY_POLICY_GATE_IDS = frozenset({"SG-1", "SG-2", "SG-3", "SG-4"})


def retire_legacy_policy_gates(state: Any) -> None:
    gates = list(getattr(state, "gates", []) or [])
    retired = list(getattr(state, "retired_gates", []) or [])
    seen = {gate.id for gate in retired}
    for gate in gates:
        if gate.id in LEGACY_POLICY_GATE_IDS and gate.id not in seen:
            retired.append(gate)
            seen.add(gate.id)
    state.retired_gates = retired
    state.gates = [gate for gate in gates if gate.id not in LEGACY_POLICY_GATE_IDS]
    proposal = getattr(state, "action_proposal", None)
    if (
        any(gate.id in LEGACY_POLICY_GATE_IDS for gate in gates)
        or getattr(proposal, "target_gate_id", "") in LEGACY_POLICY_GATE_IDS
    ):
        state.action_proposal = SupergoalActionProposal()
        state.next_best_action = ""
        state.hard_gate_reason = ""
        state.should_replan = False
        state.same_action_no_evidence_count = 0
