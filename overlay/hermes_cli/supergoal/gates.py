"""Pure gate helpers for /supergoal.

This module is intentionally small in the first migration step: it hosts gate
query predicates and selection helpers without owning GoalState mutation yet.
`goals.py` remains the facade during staged migration, but gate semantics should
move here incrementally.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any, Callable, Optional, TypeVar

GateT = TypeVar("GateT")

_PASSING_STATUSES = {"passed", "not_applicable", "followup"}
_BLOCKING_KINDS = {"run_acceptance", "domain_required", "safety_hard"}
_COMPLETION_MARKERS = (
    "complete",
    "completed",
    "done",
    "finished",
    "resolved",
    "shipped",
    "final report",
    "goal achieved",
    "mission accomplished",
)
_EVIDENCE_MARKERS = (
    "verified",
    "verification",
    "tested",
    "tests pass",
    "pytest",
    "artifact",
    "artifacts",
    "evidence",
    "changed:",
    "verified:",
    "evidence:",
    "created",
    "wrote",
    "saved",
    "report",
    "log",
    "logs",
)
_ARTIFACT_PATH_RE = re.compile(
    r"(?:(?:^|\s)(?:[./~][\w./-]+|[\w.-]+/[\w./-]+)\.(?:py|ts|tsx|js|json|md|txt|csv|log|html|yaml|yml|png|jpg|pdf))",
    re.IGNORECASE,
)


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "")
    if not value:
        return ""
    if len(value) <= limit:
        return value
    return value[:limit] + "… [truncated]"


def _clean_string_list(value: Any, *, limit: int = 12, item_limit: int = 220) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text:
            continue
        text = " ".join(text.split())
        if len(text) > item_limit:
            text = text[:item_limit].rstrip() + "…"
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _merge_compact_list(existing: list[str], new_items: Any, *, max_items: int = 20) -> list[str]:
    merged = list(existing or [])
    seen = {str(x).strip().lower() for x in merged if str(x).strip()}
    for item in _clean_string_list(new_items, limit=20):
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged[-max_items:]


def tool_backed_research_findings(state: Any) -> list[Any]:
    return [finding for finding in (getattr(state, "research_findings", []) or []) if getattr(finding, "is_tool_backed", False)]


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


def has_explicit_final_evidence(last_response: str, judge_reason: str = "") -> bool:
    """True only for final-looking responses with concrete verification/artifact language."""
    text = " ".join([last_response or "", judge_reason or ""]).strip()
    if not text:
        return False
    low = text.lower()
    has_completion = any(marker in low for marker in _COMPLETION_MARKERS)
    if not has_completion:
        return False
    has_evidence = any(marker in low for marker in _EVIDENCE_MARKERS) or bool(_ARTIFACT_PATH_RE.search(text))
    if not has_evidence:
        return False
    # Avoid treating a bare "done, see above" as proof. A credible final report
    # usually names at least two concrete dimensions: change, verification,
    # evidence, artifact, or residual state.
    marker_hits = sum(1 for marker in _EVIDENCE_MARKERS if marker in low)
    if marker_hits >= 2:
        return True
    return bool(_ARTIFACT_PATH_RE.search(text)) and any(k in low for k in ("verified", "tested", "evidence", "report"))


def reconcile_done_evidence_gates(state: Any, last_response: str, judge_reason: str) -> list[str]:
    """Pass stale generic gates when a done verdict is backed by final evidence."""
    if getattr(state, "mode", "goal") != "supergoal":
        return []
    if not has_explicit_final_evidence(last_response, judge_reason):
        return []
    passed: list[str] = []
    for gate in getattr(state, "gates", []) or []:
        if getattr(gate, "status", "") == "passed":
            continue
        gate_id = getattr(gate, "id", "")
        if gate_id == "G1":
            gate.status = "passed"
            gate.evidence = "completion report states final outcome with verification evidence"
            if not getattr(state, "inferred_user_intent", ""):
                state.inferred_user_intent = _truncate(getattr(state, "goal", ""), 300)
            if not getattr(state, "success_definition", ""):
                state.success_definition = "final response explicitly reports completion with evidence/artifacts"
            passed.append(gate_id)
        elif gate_id == "G3":
            if not has_verified_execution_evidence(state):
                set_gate_open(
                    gate,
                    missing=["tool_observed_artifact", "tool_verified_test_or_log", "human_acceptance"],
                    reason="final prose is not gate-eligible execution evidence",
                )
                continue
            gate.status = "passed"
            gate.evidence = "verified tool/human artifact or verification evidence recorded"
            passed.append(gate_id)
        elif gate_id == "G4":
            gate.status = "passed"
            gate.evidence = "completion judge plus explicit final report"
            passed.append(gate_id)
    return passed


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


def hypothesis_complete(hypothesis: Any) -> bool:
    """Return whether a strategy hypothesis has all required experiment fields."""
    return bool(
        getattr(hypothesis, "baseline", None)
        and getattr(hypothesis, "experiment", None)
        and getattr(hypothesis, "kill_criteria", None)
        and getattr(hypothesis, "artifacts", None)
        and getattr(hypothesis, "status", "") in {"passed", "failed", "killed"}
    )


def verified_hypothesis_artifact_count(state: Any) -> int:
    return sum(
        len(getattr(hypothesis, "artifacts", []) or [])
        for hypothesis in (getattr(state, "hypothesis_portfolio", []) or [])
        if hypothesis_has_verified_artifact(hypothesis)
    )


def sync_evidence_layers_from_findings(state: Any) -> bool:
    """Keep evidence_layers as a projection of provenanced findings."""
    if getattr(state, "mode", "goal") != "supergoal":
        return False
    changed = False
    layers = dict(getattr(state, "evidence_layers", {}) or {})
    external = list(layers.get("external_prior", []) or [])
    local = list(layers.get("local_empirical", []) or [])
    for finding in tool_backed_research_findings(state):
        source_type = str(getattr(finding, "source_type", "") or "")
        target = external if source_type in {"paper", "github", "web", "docs"} else local
        label = _truncate(f"{source_type}:{getattr(finding, 'title', '')}", 160)
        merged = _merge_compact_list(target, [label], max_items=12)
        if merged != target:
            target[:] = merged
            changed = True
    if external:
        layers["external_prior"] = external
    if local:
        layers["local_empirical"] = local
    if changed:
        state.evidence_layers = layers
    return changed


def evaluate_gates(
    state: Any,
    *,
    default_gate_builder: Callable[[str], list[Any]],
    ensure_gate_set: Callable[[Any], None],
) -> list[Any]:
    """Evaluate and mutate /supergoal gate statuses for the current state.

    GoalManager still supplies gate construction/upgrade callbacks during the
    staged migration, but this module owns the deterministic gate semantics.
    """
    if getattr(state, "mode", "goal") != "supergoal":
        return list(getattr(state, "gates", []) or [])
    if not getattr(state, "gates", None):
        state.gates = default_gate_builder(getattr(state, "goal", ""))
    ensure_gate_set(state)
    sync_evidence_layers_from_findings(state)
    tool_backed = tool_backed_research_findings(state)
    layers = getattr(state, "evidence_layers", {}) or {}
    external_prior_count = len(layers.get("external_prior", []) or [])
    hypotheses = list(getattr(state, "hypothesis_portfolio", []) or [])
    for gate in getattr(state, "gates", []) or []:
        gate_id = getattr(gate, "id", "")
        if gate_id == "G1":
            if getattr(state, "inferred_user_intent", "") and getattr(state, "success_definition", ""):
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "intent + success_definition populated", [], ""
            else:
                missing = []
                if not getattr(state, "inferred_user_intent", ""):
                    missing.append("inferred_user_intent")
                if not getattr(state, "success_definition", ""):
                    missing.append("success_definition")
                set_gate_open(gate, missing=missing, reason="intent contract is incomplete")
        elif gate_id == "G2":
            if getattr(state, "research_sufficiency", "") == "sufficient" and layers.get("external_prior"):
                gate.status, gate.evidence, gate.missing, gate.reason = (
                    "passed",
                    f"{len(tool_backed)} tool-backed findings; external_prior={external_prior_count}",
                    [],
                    "",
                )
            else:
                set_gate_open(
                    gate,
                    missing=["tool_backed_external_prior", "research_sufficiency=sufficient"],
                    reason="tool-backed external provenance is incomplete",
                )
        elif gate_id == "SG-1":
            if len(hypotheses) >= 3:
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", f"{len(hypotheses)} hypotheses", [], ""
            else:
                set_gate_open(gate, missing=["3 strategy hypotheses"], reason="hypothesis portfolio is too small")
        elif gate_id == "SG-2":
            if hypotheses and all(hypothesis_complete(h) for h in hypotheses):
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "all hypotheses have experiment artifacts and verdicts", [], ""
            else:
                set_gate_open(gate, missing=["baseline", "experiment", "kill_criteria", "artifact", "verdict"], reason="hypothesis verification is incomplete")
        elif gate_id == "SG-3":
            tested = [h for h in hypotheses if getattr(h, "status", "") in {"passed", "failed", "killed"}]
            has_pass = any(getattr(h, "status", "") == "passed" for h in hypotheses)
            if has_pass or (getattr(state, "no_edge_report", "") and tested and len(tested) == len(hypotheses)):
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "passed hypothesis or no-edge attribution exists", [], ""
            else:
                set_gate_open(gate, missing=["passed hypothesis", "no_edge_report if all hypotheses fail"], reason="no edge/outcome attribution is incomplete")
        elif gate_id == "SG-4":
            if getattr(state, "current_action_class", "") != "infra_engineering":
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "current action is not infrastructure", [], ""
            else:
                set_gate_open(gate, missing=["infra dependency proof"], reason="infrastructure work needs dependency proof")
        elif gate_id == "G3":
            if has_verified_execution_evidence(state):
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "verified tool/human artifact or verification evidence recorded", [], ""
            else:
                set_gate_open(
                    gate,
                    missing=["tool_observed_artifact", "tool_verified_test_or_log", "human_acceptance"],
                    reason="no gate-eligible tool/human artifact or verification evidence is recorded",
                )
        elif gate_id == "G4":
            from hermes_cli.supergoal.domain import _infer_terminal_blocker_status

            terminal_blocker = _infer_terminal_blocker_status(
                " ".join([
                    str(getattr(state, "last_verdict", "") or ""),
                    str(getattr(state, "last_reason", "") or ""),
                    " ".join(str(b) for b in (getattr(state, "blockers", []) or [])),
                ])
            )
            if getattr(state, "no_edge_report", ""):
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "no-edge outcome recorded", [], ""
            elif getattr(state, "last_verdict", "") == "done" and not terminal_blocker:
                gate.status, gate.evidence, gate.missing, gate.reason = "passed", "final evidence outcome recorded", [], ""
            else:
                set_gate_open(gate, missing=["done verdict", "final evidence mapping"], reason="final evidence/outcome mapping is missing")
    return list(getattr(state, "gates", []) or [])


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
