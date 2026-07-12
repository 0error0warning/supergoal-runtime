from __future__ import annotations

from supergoal_runtime.controller import SupergoalController
from supergoal_runtime.domain import GoalState, TurnContext
from supergoal_runtime.evaluators import CompletionJudge, EvaluatorSuite, StrategicCritic


def test_done_with_terminal_user_blocker_skips_gate_reconciliation() -> None:
    state = GoalState(
        goal="finish deployment",
        goal_run_id="gr-controller",
        mode="supergoal",
    )
    ctx = TurnContext(
        session_id="session-controller",
        state=state,
        last_response="Deployment requires user input for account authorization.",
    )
    reconciled: list[bool] = []
    controller = SupergoalController(
        evaluators=EvaluatorSuite(
            completion_judge=CompletionJudge(lambda *_a, **_k: ("done", "requires user input", False)),
            strategic_critic=StrategicCritic(lambda *_a, **_k: None, lambda *_a, **_k: None),
        ),
        apply_runtime_outcome=lambda *_a, **_k: {
            "status": "paused",
            "verdict": "blocked",
            "reason": "requires user input",
            "should_continue": False,
        },
        reconcile_done_gates=lambda *_a, **_k: reconciled.append(True),
    )

    runtime = controller._reconcile_and_decide(
        ctx,
        [],
        {
            "verdict": "done",
            "reason": "requires user input",
            "parse_failed": False,
            "wait_directive": None,
            "critic_data": None,
            "critic_applied": False,
            "critic_error": "",
            "gate_ids_before_critic": set(),
        },
    )

    assert runtime["status"] == "paused"
    assert reconciled == []
