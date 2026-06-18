"""Controller boundary for /supergoal post-turn decisions.

This is the first controller extraction: it establishes the platform-agnostic
entry point and decision DTO while delegating the historical state-machine body
back to the GoalManager facade.  Follow-up refactors can move gate/replan/
critic internals behind this boundary without changing CLI/Gateway callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict

from hermes_cli.supergoal.domain import ControllerDecision, DecisionDict, TurnContext
from hermes_cli.supergoal.evaluators import EvaluatorSuite

LegacyDecisionFn = Callable[..., DecisionDict]


@dataclass(frozen=True)
class SupergoalController:
    """Platform-neutral /supergoal controller boundary."""

    evaluators: EvaluatorSuite
    legacy_decider: LegacyDecisionFn

    def decide_after_turn(self, ctx: TurnContext) -> ControllerDecision:
        # The legacy decider is intentionally injected.  That keeps this module
        # free of GoalManager/gateway/CLI imports and makes the controller seam
        # testable before the full state machine is moved here.
        return ControllerDecision.from_dict(
            self.legacy_decider(ctx.last_response, user_initiated=ctx.user_initiated)
        )
