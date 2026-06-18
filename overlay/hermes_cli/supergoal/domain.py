"""Domain DTOs for the /supergoal runtime.

This module is intentionally small for the first extraction pass: it defines
platform-agnostic data exchanged between the public GoalManager facade and the
SupergoalController.  The large historical GoalState dataclass still lives in
``hermes_cli.goals`` until later migration steps move the full domain model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


DecisionDict = Dict[str, Any]
PromptBuilder = Callable[[], Optional[str]]
EventRecorder = Callable[[str], None]


@dataclass(frozen=True)
class TurnContext:
    """A completed assistant turn ready for goal/supergoal evaluation."""

    session_id: str
    state: Any
    last_response: str
    user_initiated: bool = True


@dataclass(frozen=True)
class ControllerDecision:
    """Platform-neutral post-turn control decision."""

    status: Optional[str]
    should_continue: bool
    continuation_prompt: Optional[str]
    verdict: str
    reason: str
    message: str = ""

    def to_dict(self) -> DecisionDict:
        return {
            "status": self.status,
            "should_continue": self.should_continue,
            "continuation_prompt": self.continuation_prompt,
            "verdict": self.verdict,
            "reason": self.reason,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: DecisionDict) -> "ControllerDecision":
        return cls(
            status=data.get("status"),
            should_continue=bool(data.get("should_continue", False)),
            continuation_prompt=data.get("continuation_prompt"),
            verdict=str(data.get("verdict") or ""),
            reason=str(data.get("reason") or ""),
            message=str(data.get("message") or ""),
        )
