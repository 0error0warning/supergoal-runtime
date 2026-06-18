"""Projection helpers for /supergoal event-derived board state.

The controller will eventually own Observe → Project.  During staged migration,
``goals.py`` still drives the full legacy path, but low-risk event projection
helpers live here so controller.project can reuse them without inheriting the
GoalManager monolith.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

MergeCompactList = Callable[[list[str], Any], list[str]]
Truncate = Callable[[Any, int], str]


def add_evidence_layer(
    state: Any,
    layer: str,
    value: str,
    *,
    merge_compact_list: Callable[..., list[str]],
    truncate: Truncate,
    max_items: int = 12,
) -> bool:
    """Add a value to ``state.evidence_layers[layer]`` and report mutation."""
    layer = str(layer or "").strip()
    value = " ".join(str(value or "").split())
    if not layer or not value:
        return False
    layers = dict(getattr(state, "evidence_layers", {}) or {})
    before = list(layers.get(layer, []) or [])
    after = merge_compact_list(before, [truncate(value, 180)], max_items=max_items)
    if after == before:
        return False
    layers[layer] = after
    state.evidence_layers = layers
    return True


def increment_failure_taxonomy(
    taxonomy: dict[str, int],
    seen_event_keys: set[tuple[str, int, str]],
    category: str,
    *,
    event_key: tuple[str, int, str],
) -> bool:
    """Increment a derived failure taxonomy category once per event key."""
    normalized = str(category or "").strip().lower()
    if not normalized or event_key in seen_event_keys:
        return False
    seen_event_keys.add(event_key)
    taxonomy[normalized] = int(taxonomy.get(normalized, 0) or 0) + 1
    return True


def apply_failure_taxonomy_policy(
    state: Any,
    *,
    merge_compact_list: Callable[..., list[str]],
) -> bool:
    """Update search/admission state when failures suggest no-edge risk."""
    changed = False
    failed_count = sum(
        1
        for hypothesis in (getattr(state, "hypothesis_portfolio", []) or [])
        if getattr(hypothesis, "status", "") in {"failed", "killed"}
    )
    failure_taxonomy = getattr(state, "failure_taxonomy", {}) or {}
    if failed_count >= 5 or len(failure_taxonomy) >= 3:
        if getattr(state, "search_phase", "") != "failure_taxonomy":
            state.search_phase = "failure_taxonomy"
            changed = True
        criteria = [
            "new hypothesis must name an independent information source, not only OHLCV/beta proxy",
            "must define baseline, kill criteria, rolling/OOS gate, and artifact before execution",
            "if next path is another failed family variant, produce no-edge attribution instead",
        ]
        before = list(getattr(state, "admission_criteria", []) or [])
        state.admission_criteria = merge_compact_list(before, criteria, max_items=8)
        changed = changed or state.admission_criteria != before
        if not getattr(state, "no_edge_report", "") and failed_count >= 8:
            state.no_edge_report = "multiple hypotheses failed; require failure taxonomy before more benchmark variants"
            changed = True
    return changed
