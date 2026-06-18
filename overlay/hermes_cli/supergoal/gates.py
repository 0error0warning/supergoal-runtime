"""Pure gate helpers for /supergoal.

This module is intentionally small in the first migration step: it hosts gate
query predicates and selection helpers without owning GoalState mutation yet.
`goals.py` remains the facade during staged migration, but gate semantics should
move here incrementally.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Optional, TypeVar

GateT = TypeVar("GateT")

_PASSING_STATUSES = {"passed", "not_applicable", "followup"}
_BLOCKING_KINDS = {"run_acceptance", "domain_required", "safety_hard"}


def is_gate_open(gate: Any) -> bool:
    """Return whether a gate still requires controller action."""
    return getattr(gate, "status", "pending") not in _PASSING_STATUSES


def is_blocking_gate(gate: Any) -> bool:
    """Return whether an open gate may veto a done verdict."""
    return bool(getattr(gate, "blocking", True)) or getattr(gate, "kind", "") in _BLOCKING_KINDS


def iter_gates(state_or_gates: Any) -> Iterable[Any]:
    """Accept either a state-like object with `.gates` or a raw gate iterable."""
    if isinstance(state_or_gates, Iterable) and not isinstance(state_or_gates, (str, bytes, dict)):
        return state_or_gates
    return getattr(state_or_gates, "gates", []) or []


def first_failed_gate(state_or_gates: Any) -> Optional[Any]:
    for gate in iter_gates(state_or_gates):
        if is_gate_open(gate):
            return gate
    return None


def first_blocking_failure(state_or_gates: Any) -> Optional[Any]:
    for gate in iter_gates(state_or_gates):
        if is_gate_open(gate) and is_blocking_gate(gate):
            return gate
    return None


def open_followups(state_or_gates: Any) -> list[Any]:
    return [gate for gate in iter_gates(state_or_gates) if is_gate_open(gate) and not is_blocking_gate(gate)]


def passed_gate_ids(state_or_gates: Any) -> set[str]:
    return {
        str(getattr(gate, "id", ""))
        for gate in iter_gates(state_or_gates)
        if getattr(gate, "status", "") == "passed"
    }


def hypothesis_has_verified_artifact(hypothesis: Any) -> bool:
    """Return whether a hypothesis artifact has verifier-like provenance.

    Critic JSON may contain ``artifacts`` and a terminal-looking status. That is
    useful board context but not execution evidence unless the artifact/verdict
    text carries a verifier marker written by a tool/human evidence path.
    """
    if not (getattr(hypothesis, "artifacts", None) and getattr(hypothesis, "status", "") in {"passed", "failed", "killed"}):
        return False
    artifacts = getattr(hypothesis, "artifacts", []) or []
    marker_text = " ".join([str(getattr(hypothesis, "verdict_reason", "") or ""), " ".join(str(a) for a in artifacts)]).lower()
    return any(
        marker in marker_text
        for marker in (
            "tool_evidence",
            "verified",
            "verification",
            "pytest",
            "test_run",
            "observed",
            "human_acceptance",
            "sha256:",
        )
    )


def verified_hypothesis_artifact_count(state: Any) -> int:
    return sum(
        len(getattr(hypothesis, "artifacts", []) or [])
        for hypothesis in (getattr(state, "hypothesis_portfolio", []) or [])
        if hypothesis_has_verified_artifact(hypothesis)
    )


def gate_eligible_evidence_count(state: Any) -> int:
    """Evidence growth metric for gates/stall guards.

    Claim-level board evidence is intentionally excluded so assistant self-report
    cannot reset no-evidence inertia.
    """
    layers = getattr(state, "evidence_layers", {}) or {}
    return (
        len(layers.get("artifact", []) or [])
        + len(layers.get("verification", []) or [])
        + len(layers.get("human_acceptance", []) or [])
        + len(layers.get("external_prior", []) or [])
        + verified_hypothesis_artifact_count(state)
    )


def has_verified_execution_evidence(state: Any) -> bool:
    """Return True only for gate-eligible G3 execution evidence."""
    layers = getattr(state, "evidence_layers", {}) or {}
    if layers.get("artifact") or layers.get("verification"):
        return True
    if any(hypothesis_has_verified_artifact(h) for h in (getattr(state, "hypothesis_portfolio", []) or [])):
        return True
    if layers.get("human_acceptance"):
        return True
    return False


def set_gate_open(gate: Any, *, missing: list[str], reason: str, truncate_limit: int = 300) -> None:
    """Mark a gate as open without clobbering already passed gates."""
    if getattr(gate, "status", "") == "passed":
        return
    gate.missing = list(missing or [])[:12]
    reason_text = " ".join(str(reason or "").split())
    gate.reason = reason_text if len(reason_text) <= truncate_limit else reason_text[:truncate_limit]
    gate.status = "pending" if is_blocking_gate(gate) else "followup"
