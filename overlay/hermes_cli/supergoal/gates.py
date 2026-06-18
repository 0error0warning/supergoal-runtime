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
