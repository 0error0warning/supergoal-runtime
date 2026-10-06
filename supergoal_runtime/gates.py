"""Task-neutral acceptance and progress checks for Supergoal.

Only an explicit task contract can require a particular kind of evidence.
Optional observations never create new acceptance criteria.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from .compat.legacy_gates import retire_legacy_policy_gates
from .domain import GoalGate, SupergoalActionProposal, _infer_terminal_blocker_status
from .projection import classify_action_text

GatePhase = Literal[
    "intent", "research", "execution", "verification", "finalization", "safety"
]
GateKind = Literal[
    "run_acceptance", "quality_followup", "safety_hard", "domain_required"
]


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
        return self.verifier_id + (
            ": " + ", ".join(self.required_evidence) if self.required_evidence else ""
        )


def default_supergoal_gate_specs(goal: str = "") -> list[GateSpec]:
    """The task's wording does not select a built-in domain workflow."""
    return [
        GateSpec(
            "G1",
            "The user's requested outcome is recorded",
            "intent",
            "run_acceptance",
            True,
            "intent_contract",
        ),
        GateSpec(
            "G2",
            "External provenance, when available",
            "research",
            "quality_followup",
            False,
            "recorded_sources",
        ),
        GateSpec(
            "G3",
            "Evidence required by the explicit task contract",
            "verification",
            "quality_followup",
            False,
            "contract_evidence",
        ),
        GateSpec(
            "G4",
            "The completion evaluation confirms the requested outcome",
            "finalization",
            "run_acceptance",
            True,
            "goal_completion",
        ),
    ]


def build_default_supergoal_gates(goal: str, gate_cls: Any) -> list[Any]:
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


_PASSING_STATUSES = {"passed", "not_applicable", "followup"}
_BLOCKING_KINDS = {"run_acceptance", "domain_required", "safety_hard"}
_EVIDENCE_LAYERS = {
    "artifact": ("artifact",),
    "verification": ("verification",),
    "external_source": ("external_prior",),
    "tool_result": (
        "tool_observation",
        "artifact",
        "verification",
        "external_prior",
        "local_empirical",
    ),
    "human_acceptance": ("human_acceptance",),
}


def is_gate_open(gate: Any) -> bool:
    return getattr(gate, "status", "pending") not in _PASSING_STATUSES


def is_blocking_gate(gate: Any) -> bool:
    return (
        bool(getattr(gate, "blocking", True))
        or getattr(gate, "kind", "") in _BLOCKING_KINDS
    )


def iter_gates(state_or_gates: Any) -> Iterable[Any]:
    if isinstance(state_or_gates, Iterable) and not isinstance(
        state_or_gates, (str, bytes, dict)
    ):
        return state_or_gates
    return getattr(state_or_gates, "gates", []) or []


def first_failed_gate(state_or_gates: Any) -> Optional[Any]:
    return next(
        (gate for gate in iter_gates(state_or_gates) if is_gate_open(gate)), None
    )


def first_blocking_failure(state_or_gates: Any) -> Optional[Any]:
    return next(
        (
            gate
            for gate in iter_gates(state_or_gates)
            if is_gate_open(gate) and is_blocking_gate(gate)
        ),
        None,
    )


def open_followups(state_or_gates: Any) -> list[Any]:
    return [
        gate
        for gate in iter_gates(state_or_gates)
        if is_gate_open(gate) and not is_blocking_gate(gate)
    ]


def passed_gate_ids(state_or_gates: Any) -> set[str]:
    return {
        str(gate.id) for gate in iter_gates(state_or_gates) if gate.status == "passed"
    }


def required_evidence(state: Any) -> list[str]:
    return list(
        getattr(getattr(state, "contract", None), "evidence_requirements", []) or []
    )


def missing_contract_evidence(state: Any) -> list[str]:
    layers = getattr(state, "evidence_layers", {}) or {}
    return [
        requirement
        for requirement in required_evidence(state)
        if not any(layers.get(layer) for layer in _EVIDENCE_LAYERS.get(requirement, ()))
    ]


def set_gate_open(
    gate: Any, *, missing: list[str], reason: str, truncate_limit: int = 300
) -> None:
    gate.missing = list(missing or [])[:12]
    gate.reason = " ".join(str(reason or "").split())[:truncate_limit]
    gate.status = "pending" if is_blocking_gate(gate) else "followup"


def _pass_gate(gate: Any, evidence: str) -> None:
    gate.status, gate.evidence, gate.missing, gate.reason = "passed", evidence, [], ""


def default_supergoal_gates(goal: str = "") -> list[GoalGate]:
    return build_default_supergoal_gates(goal, GoalGate)


def ensure_supergoal_gates_for_text(state: Any, text: str = "") -> None:
    if getattr(state, "mode", "goal") != "supergoal":
        return
    retire_legacy_policy_gates(state)
    by_id = {gate.id: gate for gate in state.gates}
    for default in default_supergoal_gates():
        gate = by_id.get(default.id)
        if gate is None:
            gate = default
            state.gates.append(gate)
        for name in (
            "description",
            "phase",
            "kind",
            "blocking",
            "verifier_id",
            "required_evidence",
            "stale_after_turns",
            "verifier",
        ):
            setattr(gate, name, getattr(default, name))
        if gate.id == "G3" and required_evidence(state):
            gate.blocking = True
            gate.kind = "run_acceptance"
            gate.required_evidence = required_evidence(state)


def evaluate_gates(
    state: Any,
    *,
    default_gate_builder: Callable[[str], list[Any]],
    ensure_gate_set: Callable[[Any], None],
) -> list[Any]:
    if getattr(state, "mode", "goal") != "supergoal":
        return list(getattr(state, "gates", []) or [])
    if not getattr(state, "gates", None):
        state.gates = default_gate_builder(getattr(state, "goal", ""))
    ensure_gate_set(state)
    layers = getattr(state, "evidence_layers", {}) or {}
    for gate in state.gates:
        if gate.id == "G1":
            outcome = getattr(
                getattr(state, "contract", None), "outcome", ""
            ) or getattr(state, "goal", "")
            if str(outcome).strip():
                _pass_gate(gate, "requested outcome recorded")
            else:
                set_gate_open(
                    gate,
                    missing=["requested outcome"],
                    reason="intent contract is incomplete",
                )
        elif gate.id == "G2":
            if layers.get("external_prior"):
                _pass_gate(gate, "tool-backed external provenance recorded")
            else:
                set_gate_open(
                    gate,
                    missing=[],
                    reason="optional tool-backed provenance has not been recorded",
                )
        elif gate.id == "G3":
            missing = missing_contract_evidence(state)
            if missing:
                set_gate_open(
                    gate,
                    missing=missing,
                    reason="task contract requires recorded tool/human evidence; prose is not proof",
                )
            elif required_evidence(state):
                _pass_gate(gate, "explicit evidence requirements are recorded")
            elif has_verified_execution_evidence(state):
                _pass_gate(gate, "optional execution evidence recorded")
            else:
                set_gate_open(
                    gate,
                    missing=[],
                    reason="no additional evidence requirements in the task contract",
                )
        elif gate.id == "G4":
            blocker = _infer_terminal_blocker_status(
                " ".join(
                    [
                        str(getattr(state, "last_verdict", "") or ""),
                        str(getattr(state, "last_reason", "") or ""),
                        " ".join(
                            str(item) for item in (getattr(state, "blockers", []) or [])
                        ),
                    ]
                )
            )
            if getattr(state, "last_verdict", "") == "done" and not blocker:
                _pass_gate(gate, "completion evaluation confirms the user's goal")
            else:
                set_gate_open(
                    gate,
                    missing=["goal completion verdict"],
                    reason="requested outcome has not been confirmed",
                )
    return list(state.gates)


def update_supergoal_gates(state: Any) -> None:
    evaluate_gates(
        state,
        default_gate_builder=default_supergoal_gates,
        ensure_gate_set=ensure_supergoal_gates_for_text,
    )


def reconcile_done_evidence_gates(
    state: Any, last_response: str, judge_reason: str
) -> list[str]:
    """Re-evaluate the explicit contract; completion prose does not grant proof."""
    before = passed_gate_ids(state)
    update_supergoal_gates(state)
    return sorted(passed_gate_ids(state) - before)


def gate_eligible_evidence_count(state: Any) -> int:
    layers = getattr(state, "evidence_layers", {}) or {}
    names = {layer for values in _EVIDENCE_LAYERS.values() for layer in values}
    return sum(len(layers.get(name, []) or []) for name in names)


def has_verified_execution_evidence(state: Any) -> bool:
    layers = getattr(state, "evidence_layers", {}) or {}
    return any(
        layers.get(name) for name in ("artifact", "verification", "human_acceptance")
    )


def fallback_action_proposal(state: Any, text: str = "") -> SupergoalActionProposal:
    gate = first_blocking_failure(state)
    action = (
        text
        or getattr(state, "next_best_action", "")
        or "Review the user's remaining acceptance criteria and take the next concrete step."
    )
    return SupergoalActionProposal(
        action_class=classify_action_text(action),
        target_gate_id=getattr(gate, "id", ""),
        text=" ".join(str(action).split())[:300],
        max_turn_budget=1,
    )


def apply_inertia_guard(state: Any, *, max_same_gate_stalls: int = 3) -> None:
    """Suggest replanning on repeated lack of progress without imposing a workflow."""
    state.hard_gate_reason = ""
    if not first_blocking_failure(state):
        state.same_action_no_evidence_count = 0
        state.last_action_evidence_count = gate_eligible_evidence_count(state)
        return
    proposal = getattr(state, "action_proposal", None)
    if proposal is None or proposal.is_empty():
        proposal = fallback_action_proposal(state)
        state.action_proposal = proposal
    history = list(getattr(state, "action_history", []) or [])
    changed = bool(history and history[-1] != proposal.action_class)
    state.current_action_class = proposal.action_class
    state.action_history = (history + [proposal.action_class])[-12:]
    count = gate_eligible_evidence_count(state)
    progress = getattr(state, "progress", "") == "real" and not required_evidence(state)
    if (
        changed
        or count > int(getattr(state, "last_action_evidence_count", 0) or 0)
        or progress
    ):
        state.same_action_no_evidence_count = 0
    else:
        state.same_action_no_evidence_count += 1
    state.last_action_evidence_count = count
    if state.same_action_no_evidence_count >= max_same_gate_stalls:
        state.hard_gate_reason = f"No new evidence or demonstrated progress after {state.same_action_no_evidence_count} repeated attempts."
        state.should_replan = True
        state.replan_count += 1
        state.next_best_action = (
            "Reassess the unmet user criteria and choose a different concrete approach."
        )
        state.action_proposal = fallback_action_proposal(state, state.next_best_action)
