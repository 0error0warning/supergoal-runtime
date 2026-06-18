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


def project_events_to_board(
    state: Any,
    events: list[Any],
    *,
    merge_compact_list: Callable[..., list[str]],
    truncate: Truncate,
    merge_research_findings: Callable[..., list[Any]],
    research_sufficiency_from_findings: Callable[[Any, str], str],
    update_gates: Callable[[Any], None],
) -> bool:
    """Derive lightweight board ledgers from structured GoalEvent payloads."""
    if state is None or getattr(state, "mode", "goal") != "supergoal":
        return False
    changed = False
    seen_event_keys: set[tuple[str, int, str]] = set()
    derived_failure_taxonomy: dict[str, int] = {}

    def add_layer(layer: str, value: str, *, max_items: int = 12) -> None:
        nonlocal changed
        changed = add_evidence_layer(
            state,
            layer,
            value,
            merge_compact_list=merge_compact_list,
            truncate=truncate,
            max_items=max_items,
        ) or changed

    def inc_failure(category: str, *, event_key: tuple[str, int, str]) -> None:
        nonlocal changed
        changed = increment_failure_taxonomy(
            derived_failure_taxonomy,
            seen_event_keys,
            category,
            event_key=event_key,
        ) or changed

    for event in events or []:
        data = getattr(event, "data", None) or {}
        etype = getattr(event, "type", "")
        summary = getattr(event, "summary", "")
        if etype == "artifact_observed":
            locator = data.get("artifact_path") or data.get("locator") or summary
            before = list(getattr(state, "evidence", []) or [])
            state.evidence = merge_compact_list(getattr(state, "evidence", []) or [], [locator], max_items=20)
            changed = changed or state.evidence != before
            if data.get("trust_level") in {"observed", "verified"} and data.get("evidence_source") != "assistant_claim":
                add_layer("artifact", locator)
        elif etype == "verification_observed":
            evidence = data.get("evidence") or summary
            before = list(getattr(state, "evidence", []) or [])
            state.evidence = merge_compact_list(getattr(state, "evidence", []) or [], [evidence], max_items=20)
            changed = changed or state.evidence != before
            if data.get("trust_level") in {"observed", "verified"} and data.get("evidence_source") != "assistant_claim":
                add_layer("verification", evidence)
        elif etype == "research_observed":
            before = list(getattr(state, "research_findings", []) or [])
            state.research_findings = merge_research_findings(getattr(state, "research_findings", []) or [], data)
            state.research_sufficiency = research_sufficiency_from_findings(state, getattr(state, "research_sufficiency", ""))
            changed = changed or state.research_findings != before
            source = str(data.get("evidence_source") or "").strip()
            trust = str(data.get("trust_level") or "").strip().lower()
            tool_call_id = str(data.get("tool_call_id") or "").strip()
            is_tool_backed = bool(tool_call_id and tool_call_id != "assistant_turn" and source != "assistant_claim" and trust in {"observed", "verified"})
            if is_tool_backed:
                add_layer("external_prior" if data.get("source_type") in {"paper", "github", "web", "docs"} else "local_empirical", summary)
        elif etype == "tool_evidence_observed":
            try:
                from hermes_cli.supergoal.evidence import EvidenceRef, research_finding_from_evidence

                ref = EvidenceRef.from_dict(data.get("evidence_ref") or data)
            except Exception:
                ref = None
                research_finding_from_evidence = None  # type: ignore[assignment]
            if ref is not None:
                locator = ref.artifact_path or ref.locator or ref.id
                if ref.trust_level in {"observed", "verified"}:
                    layer = "verification" if ref.trust_level == "verified" else "tool_observation"
                    if ref.source.value in {"web_source", "github_source"}:
                        layer = "external_prior"
                    elif ref.source.value == "file_artifact":
                        layer = "artifact"
                    elif ref.source.value == "test_run":
                        layer = "verification"
                    add_layer(layer, locator)
                    before_evidence = list(getattr(state, "evidence", []) or [])
                    state.evidence = merge_compact_list(getattr(state, "evidence", []) or [], [locator], max_items=20)
                    changed = changed or state.evidence != before_evidence
                try:
                    finding_data = research_finding_from_evidence(ref) if research_finding_from_evidence else None  # type: ignore[misc]
                except Exception:
                    finding_data = None
                if finding_data:
                    before = list(getattr(state, "research_findings", []) or [])
                    state.research_findings = merge_research_findings(getattr(state, "research_findings", []) or [], finding_data)
                    state.research_sufficiency = research_sufficiency_from_findings(state, getattr(state, "research_sufficiency", ""))
                    changed = changed or state.research_findings != before
        elif etype == "hypothesis_failed":
            add_layer("failed_hypothesis", summary)
            inc_failure(
                data.get("category") or data.get("reason") or summary,
                event_key=(etype, int(getattr(event, "turn", 0) or 0), summary),
            )
        elif etype == "action_class_observed":
            action = str(data.get("action_class") or summary or "").strip().lower()
            if action:
                before = list(getattr(state, "action_history", []) or [])
                state.action_history = (before + [action])[-12:]
                state.current_action_class = action
                changed = changed or state.action_history != before

    if derived_failure_taxonomy != (getattr(state, "failure_taxonomy", {}) or {}):
        state.failure_taxonomy = derived_failure_taxonomy
        changed = True

    changed = apply_failure_taxonomy_policy(
        state,
        merge_compact_list=merge_compact_list,
    ) or changed

    update_gates(state)
    return changed
