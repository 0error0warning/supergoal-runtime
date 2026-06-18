"""Domain DTOs for the /supergoal runtime.

This module defines platform-agnostic data exchanged between the public
GoalManager facade and the SupergoalController.  The historical GoalState
class still lives in ``hermes_cli.goals`` during the staged migration, but new
controller-facing values are explicit and typed here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Literal, Optional


ControlStatus = Literal[
    "continue",
    "done",
    "done_with_followups",
    "blocked",
    "paused_budget",
    "paused_stalled",
    "paused_judge_unhealthy",
    "paused_critic_unhealthy",
    "needs_user",
]

DecisionDict = Dict[str, Any]
PromptBuilder = Callable[[], Optional[str]]
EventRecorder = Callable[[str], None]


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
class TurnContext:
    """A completed assistant turn ready for goal/supergoal evaluation."""

    session_id: str
    state: Any
    last_response: str
    user_initiated: bool = True


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
            "evidence_refs": list(self.evidence_refs),
            "user_message": self.user_message,
            "pipeline": [asdict(s) for s in (self.snapshots or [])],
        }

    @classmethod
    def from_dict(
        cls,
        data: DecisionDict,
        *,
        gate_results: Optional[List[GateResult]] = None,
        evidence_refs: Optional[List[str]] = None,
        next_action: Optional[ActionProposal] = None,
        snapshots: Optional[List[PipelineSnapshot]] = None,
    ) -> "ControllerDecision":
        legacy_status = data.get("status")
        verdict = str(data.get("verdict") or "")
        reason = str(data.get("reason") or "")
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
    if legacy_status == "done" or verdict == "done":
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
