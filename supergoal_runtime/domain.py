"""Domain DTOs for the /supergoal runtime.

This module defines platform-agnostic data exchanged between the public
GoalManager facade and the SupergoalController.  The historical GoalState
class still lives in ``hermes_cli.goals`` during the staged migration, but new
controller-facing values are explicit and typed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from typing import Any, Callable, Dict, List, Literal, Optional, cast


ControlStatus = Literal[
    "continue",
    "done",
    "done_with_followups",
    "partial_blocked",
    "blocked",
    "paused_budget",
    "paused_stalled",
    "paused_judge_unhealthy",
    "paused_critic_unhealthy",
    "needs_user",
]

_CONTROL_STATUS_VALUES = {
    "continue",
    "done",
    "done_with_followups",
    "partial_blocked",
    "blocked",
    "paused_budget",
    "paused_stalled",
    "paused_judge_unhealthy",
    "paused_critic_unhealthy",
    "needs_user",
}

DecisionDict = Dict[str, Any]
PromptBuilder = Callable[[], Optional[str]]
EventRecorder = Callable[[str], None]

DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_SAME_GATE_STALLS = 3
_ALLOWED_ACTION_CLASSES = {
    "research",
    "hypothesis_generation",
    "experiment_execution",
    "validation",
    "infra_engineering",
    "reporting",
    "safety",
    "unknown",
}


def _truncate(text: Any, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "..."


def _clean_string_list(value: Any, *, limit: int = 20, item_limit: int = 220) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if len(text) > item_limit:
            text = text[:item_limit].rstrip() + "..."
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return 0.0
    if parsed > 1:
        return 1.0
    return parsed


def _default_gate_metadata(gate_id: str) -> tuple[str, str, bool]:
    gid = (gate_id or "").upper()
    if gid == "G1":
        return "intent", "run_acceptance", True
    if gid == "G2":
        return "research", "domain_required", True
    if gid == "G3":
        return "execution", "run_acceptance", True
    if gid == "G4":
        return "finalization", "run_acceptance", True
    if gid.startswith("SG-"):
        return "verification", "domain_required", True
    if gid.startswith("SAFE") or gid.startswith("SAFETY"):
        return "safety", "safety_hard", True
    if gid.startswith("MON"):
        return "verification", "quality_followup", False
    return "verification", "run_acceptance", True


@dataclass
class GoalEvent:
    ts: float
    type: str
    turn: int
    summary: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GoalContract:
    outcome: str = ""
    verification: str = ""
    constraints: str = ""
    boundaries: str = ""
    stop_when: str = ""

    def is_empty(self) -> bool:
        return not any(
            getattr(self, field_name).strip()
            for field_name in ("outcome", "verification", "constraints", "boundaries", "stop_when")
        )

    @classmethod
    def from_dict(cls, data: Any) -> "GoalContract":
        if not isinstance(data, dict):
            return cls()
        return cls(
            outcome=str(data.get("outcome") or "").strip(),
            verification=str(data.get("verification") or "").strip(),
            constraints=str(data.get("constraints") or "").strip(),
            boundaries=str(data.get("boundaries") or "").strip(),
            stop_when=str(data.get("stop_when") or "").strip(),
        )


@dataclass
class PlanStep:
    id: str
    title: str
    status: str = "pending"
    verification: str = ""
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["PlanStep"]:
        if not isinstance(data, dict):
            return None
        step_id = str(data.get("id") or "").strip()
        title = str(data.get("title") or "").strip()
        if not step_id or not title:
            return None
        status = str(data.get("status") or "pending").strip()
        if status not in {"pending", "in_progress", "done", "failed", "blocked", "skipped"}:
            status = "pending"
        return cls(
            id=step_id,
            title=title,
            status=status,
            verification=str(data.get("verification") or "").strip(),
            summary=str(data.get("summary") or "").strip(),
        )


@dataclass
class ResearchFinding:
    source_type: str
    title: str
    locator: str = ""
    claim: str = ""
    retrieved_at: str = ""
    tool_call_id: str = ""
    query: str = ""
    evidence_quote_or_hash: str = ""
    evidence_source: str = "assistant_claim"
    trust_level: str = "claim"
    relevance_score: float = 0.0
    contradiction: bool = False

    @property
    def is_tool_backed(self) -> bool:
        if self.tool_call_id == "assistant_turn" or self.evidence_source == "assistant_claim" or self.trust_level == "claim":
            return False
        return bool(self.tool_call_id and self.trust_level in {"observed", "verified"})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, *, infer_legacy_tool_backed: bool = False) -> Optional["ResearchFinding"]:
        if not isinstance(data, dict):
            return None
        source_type = str(data.get("source_type") or data.get("type") or "").strip().lower()
        title = str(data.get("title") or "").strip()
        if not source_type or not title:
            return None
        if source_type not in {"paper", "github", "web", "docs", "repo", "benchmark", "local", "other"}:
            source_type = "other"
        tool_call_id = _truncate(" ".join(str(data.get("tool_call_id") or "").split()), 120)
        evidence_quote_or_hash = _truncate(
            " ".join(str(data.get("evidence_quote_or_hash") or data.get("quote") or data.get("hash") or "").split()),
            400,
        )
        legacy_tool_backed = bool(
            infer_legacy_tool_backed
            and tool_call_id
            and tool_call_id != "assistant_turn"
            and evidence_quote_or_hash
        )
        evidence_source = str(data.get("evidence_source") or data.get("source") or "").strip() or (
            "tool_call" if legacy_tool_backed else "assistant_claim"
        )
        trust_level = str(data.get("trust_level") or "").strip() or ("observed" if legacy_tool_backed else "claim")
        return cls(
            source_type=source_type,
            title=_truncate(" ".join(title.split()), 160),
            locator=_truncate(" ".join(str(data.get("locator") or data.get("url_or_path") or "").split()), 240),
            claim=_truncate(" ".join(str(data.get("claim") or "").split()), 240),
            retrieved_at=_truncate(" ".join(str(data.get("retrieved_at") or "").split()), 80),
            tool_call_id=tool_call_id,
            query=_truncate(" ".join(str(data.get("query") or "").split()), 240),
            evidence_quote_or_hash=evidence_quote_or_hash,
            evidence_source=_truncate(" ".join(evidence_source.split()), 80),
            trust_level=_truncate(" ".join(trust_level.split()), 20),
            relevance_score=_coerce_float(data.get("relevance_score"), 0.0),
            contradiction=bool(data.get("contradiction", False)),
        )


@dataclass
class HypothesisRecord:
    id: str
    claim: str
    why_plausible: str = ""
    data_needed: str = ""
    baseline: str = ""
    experiment: str = ""
    kill_criteria: str = ""
    expected_edge: str = ""
    risk: str = ""
    status: str = "proposed"
    verdict_reason: str = ""
    artifacts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["HypothesisRecord"]:
        if isinstance(data, str):
            text = " ".join(data.split())
            return cls(id="H?", claim=_truncate(text, 240)) if text else None
        if not isinstance(data, dict):
            return None
        claim = str(data.get("claim") or data.get("hypothesis") or "").strip()
        if not claim:
            return None
        status = str(data.get("status") or "proposed").strip().lower()
        if status not in {"proposed", "running", "passed", "failed", "killed"}:
            status = "proposed"
        return cls(
            id=_truncate(str(data.get("id") or data.get("name") or "H?").strip() or "H?", 32),
            claim=_truncate(" ".join(claim.split()), 240),
            why_plausible=_truncate(" ".join(str(data.get("why_plausible") or "").split()), 240),
            data_needed=_truncate(" ".join(str(data.get("data_needed") or "").split()), 240),
            baseline=_truncate(" ".join(str(data.get("baseline") or "").split()), 240),
            experiment=_truncate(" ".join(str(data.get("experiment") or "").split()), 300),
            kill_criteria=_truncate(" ".join(str(data.get("kill_criteria") or "").split()), 240),
            expected_edge=_truncate(" ".join(str(data.get("expected_edge") or "").split()), 160),
            risk=_truncate(" ".join(str(data.get("risk") or "").split()), 160),
            status=status,
            verdict_reason=_truncate(" ".join(str(data.get("verdict_reason") or "").split()), 240),
            artifacts=_clean_string_list(data.get("artifacts") or [], limit=8, item_limit=180),
        )


@dataclass
class GoalGate:
    id: str
    description: str
    status: str = "pending"
    verifier: str = ""
    evidence: str = ""
    phase: str = "verification"
    kind: str = "run_acceptance"
    blocking: bool = True
    verifier_id: str = ""
    required_evidence: list[str] = field(default_factory=list)
    stale_after_turns: Optional[int] = None
    missing: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> Optional["GoalGate"]:
        if not isinstance(data, dict):
            return None
        gid = str(data.get("id") or "").strip()
        desc = str(data.get("description") or data.get("title") or "").strip()
        if not gid or not desc:
            return None
        phase, kind, default_blocking = _default_gate_metadata(gid)
        phase = str(data.get("phase") or phase).strip().lower()
        kind = str(data.get("kind") or kind).strip().lower()
        blocking = bool(data.get("blocking", default_blocking))
        if kind in {"run_acceptance", "domain_required", "safety_hard"}:
            blocking = True
        if kind == "quality_followup":
            blocking = False
        return cls(
            id=_truncate(gid, 40),
            description=_truncate(" ".join(desc.split()), 240),
            status=str(data.get("status") or "pending").strip().lower(),
            verifier=_truncate(" ".join(str(data.get("verifier") or "").split()), 240),
            evidence=_truncate(" ".join(str(data.get("evidence") or "").split()), 300),
            phase=phase,
            kind=kind,
            blocking=blocking,
            verifier_id=_truncate(" ".join(str(data.get("verifier_id") or data.get("verifier") or "").split()), 120),
            required_evidence=_clean_string_list(data.get("required_evidence") or [], limit=12, item_limit=80),
            stale_after_turns=data.get("stale_after_turns") if isinstance(data.get("stale_after_turns"), int) else None,
            missing=_clean_string_list(data.get("missing") or [], limit=12, item_limit=120),
            reason=_truncate(" ".join(str(data.get("reason") or "").split()), 300),
        )


@dataclass
class SupergoalActionProposal:
    action_class: str = "unknown"
    target_gate_id: str = ""
    expected_evidence: list[str] = field(default_factory=list)
    tools_needed: list[str] = field(default_factory=list)
    max_turn_budget: int = 1
    risk_level: str = "medium"
    why_this_gate_first: str = ""
    stop_if: list[str] = field(default_factory=list)
    text: str = ""
    override_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "SupergoalActionProposal":
        if not isinstance(data, dict):
            return cls()
        action_class = str(data.get("action_class") or "unknown").strip().lower() or "unknown"
        if action_class not in _ALLOWED_ACTION_CLASSES:
            action_class = "unknown"
        risk = str(data.get("risk_level") or "medium").strip().lower() or "medium"
        if risk not in {"low", "medium", "high"}:
            risk = "medium"
        try:
            budget = int(data.get("max_turn_budget", 1) or 1)
        except (TypeError, ValueError):
            budget = 1
        return cls(
            action_class=action_class,
            target_gate_id=_truncate(" ".join(str(data.get("target_gate_id") or "").split()), 40),
            expected_evidence=_clean_string_list(data.get("expected_evidence") or [], limit=8, item_limit=120),
            tools_needed=_clean_string_list(data.get("tools_needed") or [], limit=8, item_limit=80),
            max_turn_budget=max(1, min(10, budget)),
            risk_level=risk,
            why_this_gate_first=_truncate(" ".join(str(data.get("why_this_gate_first") or data.get("why") or "").split()), 240),
            stop_if=_clean_string_list(data.get("stop_if") or [], limit=8, item_limit=120),
            text=_truncate(" ".join(str(data.get("text") or data.get("next_best_action") or "").split()), 300),
            override_reason=_truncate(" ".join(str(data.get("override_reason") or "").split()), 240),
        )

    def is_empty(self) -> bool:
        return not (self.action_class != "unknown" or self.target_gate_id or self.text)


@dataclass
class GoalState:
    goal: str
    goal_run_id: str = ""
    mode: str = "supergoal"
    status: str = "active"
    turns_used: int = 0
    max_turns: int = DEFAULT_MAX_TURNS
    created_at: float = 0.0
    last_turn_at: float = 0.0
    last_verdict: Optional[str] = None
    last_reason: Optional[str] = None
    paused_reason: Optional[str] = None
    consecutive_parse_failures: int = 0
    consecutive_judge_api_failures: int = 0
    consecutive_critic_failures: int = 0
    last_failed_gate_id: str = ""
    same_gate_stall_count: int = 0
    last_continuation_enqueued_at: float = 0.0
    last_continuation_kind: Optional[str] = None
    acceptance_criteria: list[str] = field(default_factory=list)
    milestones: list[str] = field(default_factory=list)
    hypotheses: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    attempted_solutions: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    inferred_user_intent: str = ""
    success_definition: str = ""
    first_principles_model: list[str] = field(default_factory=list)
    existing_solution_scan: list[str] = field(default_factory=list)
    research_findings: list[ResearchFinding] = field(default_factory=list)
    hypothesis_portfolio: list[HypothesisRecord] = field(default_factory=list)
    gates: list[GoalGate] = field(default_factory=list)
    action_history: list[str] = field(default_factory=list)
    current_action_class: str = "unknown"
    action_proposal: SupergoalActionProposal = field(default_factory=SupergoalActionProposal)
    last_action_evidence_count: int = 0
    same_action_no_evidence_count: int = 0
    hard_gate_reason: str = ""
    no_edge_report: str = ""
    build_vs_reuse_decision: str = ""
    evidence_layers: dict[str, list[str]] = field(default_factory=dict)
    failure_taxonomy: dict[str, int] = field(default_factory=dict)
    search_phase: str = "explore"
    admission_criteria: list[str] = field(default_factory=list)
    literalism_risk: str = "unknown"
    research_sufficiency: str = "unknown"
    next_best_action: str = ""
    strategy_health: str = "unknown"
    progress: str = "unknown"
    root_cause_confidence: float = 0.0
    should_replan: bool = False
    replan_count: int = 0
    plan_steps: list[PlanStep] = field(default_factory=list)
    current_step_id: str = ""
    permission_mode: str = "supervised"
    permission_contract: dict[str, Any] = field(default_factory=dict)
    event_count: int = 0
    last_event_type: Optional[str] = None
    status_card_message_ids: dict[str, str] = field(default_factory=dict)
    subgoals: list[str] = field(default_factory=list)
    waiting_on_pid: Optional[int] = None
    waiting_on_session: Optional[str] = None
    waiting_until: float = 0.0
    waiting_reason: Optional[str] = None
    waiting_since: float = 0.0
    contract: GoalContract = field(default_factory=GoalContract)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "GoalState":
        data = json.loads(raw)
        raw_findings = data.get("research_findings") or []
        findings = [
            finding
            for item in raw_findings
            if (finding := ResearchFinding.from_dict(item, infer_legacy_tool_backed=True)) is not None
        ] if isinstance(raw_findings, list) else []
        raw_hypotheses = data.get("hypothesis_portfolio") or data.get("hypotheses_detail") or []
        hypotheses = [
            hypothesis
            for item in raw_hypotheses
            if (hypothesis := HypothesisRecord.from_dict(item)) is not None
        ] if isinstance(raw_hypotheses, list) else []
        raw_gates = data.get("gates") or []
        gates = [
            gate
            for item in raw_gates
            if (gate := GoalGate.from_dict(item)) is not None
        ] if isinstance(raw_gates, list) else []
        raw_steps = data.get("plan_steps") or []
        steps = [
            step
            for item in raw_steps
            if (step := PlanStep.from_dict(item)) is not None
        ] if isinstance(raw_steps, list) else []
        return cls(
            goal=str(data.get("goal") or ""),
            goal_run_id=str(data.get("goal_run_id") or ""),
            mode=str(data.get("mode") or "supergoal"),
            status=str(data.get("status") or "active"),
            turns_used=int(data.get("turns_used", 0) or 0),
            max_turns=int(data.get("max_turns", DEFAULT_MAX_TURNS) or DEFAULT_MAX_TURNS),
            created_at=float(data.get("created_at", 0.0) or 0.0),
            last_turn_at=float(data.get("last_turn_at", 0.0) or 0.0),
            last_verdict=data.get("last_verdict"),
            last_reason=data.get("last_reason"),
            paused_reason=data.get("paused_reason"),
            consecutive_parse_failures=int(data.get("consecutive_parse_failures", 0) or 0),
            consecutive_judge_api_failures=int(data.get("consecutive_judge_api_failures", 0) or 0),
            consecutive_critic_failures=int(data.get("consecutive_critic_failures", 0) or 0),
            last_failed_gate_id=str(data.get("last_failed_gate_id") or ""),
            same_gate_stall_count=int(data.get("same_gate_stall_count", 0) or 0),
            last_continuation_enqueued_at=float(data.get("last_continuation_enqueued_at", 0.0) or 0.0),
            last_continuation_kind=data.get("last_continuation_kind"),
            acceptance_criteria=_clean_string_list(data.get("acceptance_criteria") or []),
            milestones=_clean_string_list(data.get("milestones") or []),
            hypotheses=_clean_string_list(data.get("hypotheses") or []),
            evidence=_clean_string_list(data.get("evidence") or []),
            attempted_solutions=_clean_string_list(data.get("attempted_solutions") or []),
            blockers=_clean_string_list(data.get("blockers") or []),
            risks=_clean_string_list(data.get("risks") or []),
            inferred_user_intent=str(data.get("inferred_user_intent") or ""),
            success_definition=str(data.get("success_definition") or ""),
            first_principles_model=_clean_string_list(data.get("first_principles_model") or []),
            existing_solution_scan=_clean_string_list(data.get("existing_solution_scan") or []),
            research_findings=findings,
            hypothesis_portfolio=hypotheses,
            gates=gates,
            action_history=_clean_string_list(data.get("action_history") or [], limit=12, item_limit=80),
            current_action_class=str(data.get("current_action_class") or "unknown"),
            action_proposal=SupergoalActionProposal.from_dict(data.get("action_proposal") or {}),
            last_action_evidence_count=int(data.get("last_action_evidence_count", 0) or 0),
            same_action_no_evidence_count=int(data.get("same_action_no_evidence_count", 0) or 0),
            hard_gate_reason=str(data.get("hard_gate_reason") or ""),
            no_edge_report=str(data.get("no_edge_report") or ""),
            build_vs_reuse_decision=str(data.get("build_vs_reuse_decision") or ""),
            evidence_layers={
                str(k): _clean_string_list(v, limit=12, item_limit=160)
                for k, v in (data.get("evidence_layers") or {}).items()
                if str(k).strip()
            } if isinstance(data.get("evidence_layers"), dict) else {},
            failure_taxonomy={
                str(k): int(v or 0)
                for k, v in (data.get("failure_taxonomy") or {}).items()
                if str(k).strip()
            } if isinstance(data.get("failure_taxonomy"), dict) else {},
            search_phase=str(data.get("search_phase") or "explore"),
            admission_criteria=_clean_string_list(data.get("admission_criteria") or [], limit=8, item_limit=180),
            literalism_risk=str(data.get("literalism_risk") or "unknown"),
            research_sufficiency=str(data.get("research_sufficiency") or "unknown"),
            next_best_action=str(data.get("next_best_action") or ""),
            strategy_health=str(data.get("strategy_health") or "unknown"),
            progress=str(data.get("progress") or "unknown"),
            root_cause_confidence=_coerce_float(data.get("root_cause_confidence"), 0.0),
            should_replan=bool(data.get("should_replan", False)),
            replan_count=int(data.get("replan_count", 0) or 0),
            plan_steps=steps,
            current_step_id=str(data.get("current_step_id") or ""),
            permission_mode=str(data.get("permission_mode") or "supervised"),
            permission_contract=dict(data.get("permission_contract") or {}) if isinstance(data.get("permission_contract"), dict) else {},
            event_count=int(data.get("event_count", 0) or 0),
            last_event_type=data.get("last_event_type"),
            status_card_message_ids={str(k): str(v) for k, v in (data.get("status_card_message_ids") or {}).items()} if isinstance(data.get("status_card_message_ids"), dict) else {},
            subgoals=_clean_string_list(data.get("subgoals") or []),
            waiting_on_pid=int(data["waiting_on_pid"]) if data.get("waiting_on_pid") is not None else None,
            waiting_on_session=str(data.get("waiting_on_session") or "") or None,
            waiting_until=float(data.get("waiting_until", 0.0) or 0.0),
            waiting_reason=str(data.get("waiting_reason") or "") or None,
            waiting_since=float(data.get("waiting_since", 0.0) or 0.0),
            contract=GoalContract.from_dict(data.get("contract")),
        )

    def has_contract(self) -> bool:
        return self.contract is not None and not self.contract.is_empty()

    def render_subgoals_block(self) -> str:
        return "\n".join(f"- {i}. {text}" for i, text in enumerate(self.subgoals, start=1))

    def render_supergoal_board(self) -> str:
        parts = [
            f"objective: {self.goal}",
            f"progress: {self.progress}; strategy_health: {self.strategy_health}; root_cause_confidence: {self.root_cause_confidence:.2f}",
            f"literalism_risk: {self.literalism_risk}; research_sufficiency: {self.research_sufficiency}",
        ]
        if self.inferred_user_intent:
            parts.append("inferred_user_intent: " + self.inferred_user_intent)
        if self.success_definition:
            parts.append("success_definition: " + self.success_definition)
        if self.gates:
            parts.append("gates: " + "; ".join(f"{g.id}:{g.status}:{g.description}" for g in self.gates[:8]))
        if self.action_history:
            parts.append("action_history: " + " -> ".join(self.action_history[-8:]))
        if self.hard_gate_reason:
            parts.append("hard_gate: " + self.hard_gate_reason)
        if self.evidence_layers:
            parts.append("evidence_layers: " + "; ".join(f"{k}={len(v)}" for k, v in sorted(self.evidence_layers.items())))
        if self.next_best_action:
            parts.append("next_best_action: " + self.next_best_action)
        if self.should_replan:
            parts.append("replan: required")
        return "\n".join(parts)


@dataclass(frozen=True)
class ActionProposal:
    """A controller-visible next action proposal.

    The controller should approve structured proposals rather than infer action
    semantics from prose. ``text`` is retained as a compact legacy summary.
    """

    text: str
    action_class: str = "unknown"
    target_gate_id: str = ""
    expected_evidence: List[str] | None = None
    tools_needed: List[str] | None = None
    max_turn_budget: int = 1
    risk_level: str = "medium"
    why_this_gate_first: str = ""
    stop_if: List[str] | None = None
    override_reason: str = ""
    why: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["expected_evidence"] = list(self.expected_evidence or [])
        data["tools_needed"] = list(self.tools_needed or [])
        data["stop_if"] = list(self.stop_if or [])
        return data


GateStatus = Literal["passed", "failed", "blocked", "not_applicable", "followup", "pending"]


@dataclass(frozen=True)
class GateResult:
    """Snapshot of a deterministic gate after reconciliation."""

    gate_id: str
    status: GateStatus
    blocking: bool
    evidence_refs: List[str]
    missing: List[str]
    reason: str
    description: str = ""
    phase: str = "verification"
    kind: str = "run_acceptance"

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["id"] = self.gate_id  # legacy alias for earlier typed surface
        return data


@dataclass(frozen=True)
class GateDecision:
    """Controller-level summary of how gates affected a post-turn decision."""

    gate_vetoed: bool = False
    first_blocking_gate_id: str = ""
    first_blocking_gate_description: str = ""
    followup_gate_ids: List[str] | None = None
    done_with_followups: bool = False
    stalled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["followup_gate_ids"] = list(self.followup_gate_ids or [])
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any] | None) -> "GateDecision":
        if not isinstance(data, dict):
            return cls()
        return cls(
            gate_vetoed=bool(data.get("gate_vetoed", False)),
            first_blocking_gate_id=str(data.get("first_blocking_gate_id") or ""),
            first_blocking_gate_description=str(data.get("first_blocking_gate_description") or ""),
            followup_gate_ids=[str(x) for x in (data.get("followup_gate_ids") or []) if str(x)],
            done_with_followups=bool(data.get("done_with_followups", False)),
            stalled=bool(data.get("stalled", False)),
        )


@dataclass(frozen=True)
class TurnContext:
    """A completed assistant turn ready for goal/supergoal evaluation."""

    session_id: str
    state: Any
    last_response: str
    user_initiated: bool = True
    background_processes: Optional[List[Dict[str, Any]]] = None


@dataclass(frozen=True)
class PipelineSnapshot:
    """Small debug/telemetry payload for each controller phase."""

    phase: Literal["observe", "project", "evaluate", "reconcile", "decide", "render"]
    summary: str = ""
    data: Dict[str, Any] | None = None


@dataclass(frozen=True)
class ControllerDecision:
    """Platform-neutral post-turn control decision.

    ``status`` is the new typed control status. ``legacy_status`` preserves the
    old GoalManager/Gateway contract (``active``/``done``/``paused``/etc.) and
    is emitted as ``dict['status']`` for compatibility.
    """

    status: ControlStatus
    should_continue: bool
    next_action: Optional[ActionProposal]
    continuation_prompt: Optional[str]
    gate_results: List[GateResult]
    evidence_refs: List[str]
    reason: str
    gate_decision: GateDecision = field(default_factory=GateDecision)
    user_message: str = ""
    legacy_status: Optional[str] = None
    verdict: str = "continue"
    snapshots: List[PipelineSnapshot] | None = None

    def to_dict(self) -> DecisionDict:
        return {
            # Backwards-compatible surface consumed by CLI/Gateway/tests.
            "status": self.legacy_status or self.status,
            "should_continue": self.should_continue,
            "continuation_prompt": self.continuation_prompt,
            "verdict": self.verdict,
            "reason": self.reason,
            "message": self.user_message,
            # New typed controller surface.
            "control_status": self.status,
            "next_action": self.next_action.to_dict() if self.next_action else None,
            "gate_results": [g.to_dict() for g in self.gate_results],
            "gate_decision": self.gate_decision.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "user_message": self.user_message,
            "followup_gate_ids": [
                g.gate_id for g in self.gate_results if g.status == "followup" and not g.blocking
            ] if self.status == "done_with_followups" else [],
            "pipeline": [asdict(s) for s in (self.snapshots or [])],
        }

    @classmethod
    def from_dict(
        cls,
        data: DecisionDict,
        *,
        gate_results: Optional[List[GateResult]] = None,
        gate_decision: Optional[GateDecision] = None,
        evidence_refs: Optional[List[str]] = None,
        next_action: Optional[ActionProposal] = None,
        snapshots: Optional[List[PipelineSnapshot]] = None,
    ) -> "ControllerDecision":
        legacy_status = data.get("status")
        verdict = str(data.get("verdict") or "")
        reason = str(data.get("reason") or "")
        explicit_control_status = str(data.get("control_status") or "")
        if explicit_control_status in _CONTROL_STATUS_VALUES:
            control_status = cast(ControlStatus, explicit_control_status)
        else:
            control_status = _infer_control_status(
                legacy_status=str(legacy_status or ""),
                verdict=verdict,
                reason=reason,
                should_continue=bool(data.get("should_continue", False)),
                message=str(data.get("message") or ""),
            )
        return cls(
            status=control_status,
            should_continue=bool(data.get("should_continue", False)),
            next_action=next_action,
            continuation_prompt=data.get("continuation_prompt"),
            gate_results=gate_results or [],
            gate_decision=gate_decision or GateDecision.from_dict(data.get("gate_decision")),
            evidence_refs=evidence_refs or [],
            reason=reason,
            user_message=str(data.get("message") or data.get("user_message") or ""),
            legacy_status=legacy_status,
            verdict=verdict,
            snapshots=snapshots or [],
        )


def _infer_control_status(
    *,
    legacy_status: str,
    verdict: str,
    reason: str,
    should_continue: bool,
    message: str,
) -> ControlStatus:
    text = " ".join([legacy_status, verdict, reason, message]).lower()
    terminal_blocker = _infer_terminal_blocker_status(text)
    if terminal_blocker:
        return terminal_blocker
    if legacy_status == "done" or verdict == "done":
        if "follow-up gates open" in text or "followup gates open" in text:
            return "done_with_followups"
        return "done"
    if should_continue:
        return "continue"
    if "budget" in text or "turn budget" in text:
        return "paused_budget"
    if "stalled" in text or "same_gate_stall" in text:
        return "paused_stalled"
    if "judge api" in text or "judge model" in text or "parse" in text:
        return "paused_judge_unhealthy"
    if "critic" in text or "board update" in text:
        return "paused_critic_unhealthy"
    if "blocked" in text:
        return "blocked"
    if "user" in text and ("input" in text or "need" in text):
        return "needs_user"
    return "blocked" if legacy_status == "paused" else "continue"


_TERMINAL_BLOCKER_MARKERS = (
    "live_forbidden",
    "policy deny",
    "policy denied",
    "policy denial",
    "blocked by policy",
    "denied by policy",
    "disallowed by policy",
    "security policy",
    "safety policy",
    "cannot continue until",
    "can't continue until",
    "unable to continue until",
    "could not continue until",
)

_PERMISSION_DENIED_ACTIVE_RE = re.compile(
    r"\b(?:blocked|stopped|paused|prevented|unable|cannot|can't|could not)\b[^.?!;]{0,80}\bpermission denied\b"
    r"|\bpermission denied\b[^.?!;]{0,80}\b(?:blocks?|blocked|prevents?|prevented|stops?|stopped|cannot|can't|unable|could not)\b",
    re.IGNORECASE,
)

_NEEDS_USER_MARKERS = (
    "needs user input",
    "need user input",
    "needs input from the user",
    "need input from the user",
    "requires user input",
    "requires human input",
    "waiting for user",
)


def _infer_terminal_blocker_status(text: str) -> ControlStatus | None:
    """Classify terminal-looking blocker prose before DONE wins.

    ``done`` means successful convergence.  Long-running /supergoal loops need
    a separate terminal control surface for partial results stopped by policy,
    permissions, or required human input.  Keep the markers intentionally
    specific so phrases like "blocked/no-edge outcome recorded" in gate labels
    do not accidentally demote a legitimate no-edge completion.
    """
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _NEEDS_USER_MARKERS):
        return "needs_user"
    resolved_markers = (
        "resolved",
        "resolving",
        "completed after",
        "successfully completed",
        "fixed the permission denied",
        "fixed permission denied",
    )
    if not any(marker in lowered for marker in resolved_markers):
        if any(marker in lowered for marker in _TERMINAL_BLOCKER_MARKERS):
            return "partial_blocked"
    if _PERMISSION_DENIED_ACTIVE_RE.search(lowered):
        return "partial_blocked"
    return None
