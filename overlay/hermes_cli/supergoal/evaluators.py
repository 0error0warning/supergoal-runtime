"""Evaluator adapters for the /supergoal runtime.

The concrete judge/critic functions remain in ``hermes_cli.goals`` for this
migration step so existing tests can still monkeypatch them.  These adapters
make the controller depend on explicit evaluator objects instead of reaching
through GoalManager for everything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

JudgeResult = Tuple[str, str, bool]
JudgeFn = Callable[[str, str], JudgeResult]
CriticFn = Callable[[Any, str], Optional[Dict[str, Any]]]
CriticApplyFn = Callable[[Any, Optional[Dict[str, Any]]], None]


@dataclass(frozen=True)
class CompletionJudge:
    """Adapter around the completion judge callable."""

    judge_fn: Callable[..., JudgeResult]

    def evaluate(self, goal: str, last_response: str, *, subgoals: Optional[List[str]] = None) -> JudgeResult:
        return self.judge_fn(goal, last_response, subgoals=subgoals or None)


@dataclass(frozen=True)
class StrategicCritic:
    """Adapter around the strategic critic + merge callables."""

    critic_fn: Callable[[Any, str], Optional[Dict[str, Any]]]
    apply_fn: Callable[[Any, Optional[Dict[str, Any]]], None]

    def evaluate(self, state: Any, last_response: str) -> Optional[Dict[str, Any]]:
        return self.critic_fn(state, last_response)

    def apply(self, state: Any, data: Optional[Dict[str, Any]]) -> None:
        self.apply_fn(state, data)


@dataclass(frozen=True)
class EvaluatorSuite:
    completion_judge: CompletionJudge
    strategic_critic: StrategicCritic
