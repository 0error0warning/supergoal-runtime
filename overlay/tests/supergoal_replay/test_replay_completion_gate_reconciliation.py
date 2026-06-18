def test_replay_completion_done_with_acceptance_gates_does_not_enqueue(trace, replay):
    result = replay(trace("ai6_completion_gate_conflict.jsonl"))
    decision = result.decisions[-1]
    state = result.managers["replay-ai6"].state

    assert state is not None
    assert state.status == "done"
    assert decision["status"] == "done"
    assert decision["control_status"] == "done"
    assert decision["should_continue"] is False
    assert decision["continuation_prompt"] is None
    assert state.should_replan is False
    assert not state.next_best_action


def test_replay_only_monitoring_gates_done_with_followups(_isolate_hermes_home):
    from unittest.mock import patch
    from hermes_cli import goals
    from hermes_cli.goals import GoalGate, GoalManager, save_goal

    mgr = GoalManager(session_id="replay-followups")
    state = mgr.set("write a small verified artifact", max_turns=10, mode="supergoal")
    state.evidence_layers = {"artifact": ["/tmp/replay-small.md"]}
    state.gates.append(
        GoalGate(
            id="MON-1",
            description="Optional post-run monitoring followup",
            status="pending",
            phase="verification",
            kind="quality_followup",
            blocking=False,
            missing=["monitoring followup"],
        )
    )
    save_goal("replay-followups", state)
    with patch.object(goals, "judge_goal", return_value=("done", "artifact satisfies request", False)), patch.object(
        goals,
        "critic_supergoal",
        return_value={
            "progress": "real",
            "strategy_health": "good",
            "success_definition": "artifact exists and is summarized",
            "inferred_user_intent": "get the artifact",
            "first_principles_model": ["small artifact can be accepted without broad research"],
            "existing_solution_scan": ["not required for this tiny artifact"],
            "new_evidence": ["artifact: /tmp/replay-small.md"],
            "action_proposal": {"action_class": "reporting", "target_gate_id": "G4", "text": "finalize"},
        },
    ):
        decision = mgr.evaluate_after_turn("Created /tmp/replay-small.md and mapped it to the request.")

    assert decision["status"] == "done"
    assert decision["control_status"] == "done_with_followups"
    assert set(decision["followup_gate_ids"]) == {"G2", "MON-1"}
    assert decision["should_continue"] is False
    reconcile_phase = next(p for p in decision["pipeline"] if p["phase"] == "reconcile")
    gate_decision = reconcile_phase["data"]["gate_decision"]
    assert reconcile_phase["data"]["done_gate_precomputed"] is True
    assert gate_decision["done_with_followups"] is True
    assert set(gate_decision["followup_gate_ids"]) == {"G2", "MON-1"}
    assert decision["gate_decision"] == gate_decision
    mon = next(g for g in mgr.state.gates if g.id == "MON-1")
    assert mon.status == "followup"
    assert not mon.evidence


def test_replay_status_line_and_card_match_internal_status(trace, replay):
    result = replay(trace("ai6_completion_gate_conflict.jsonl"))
    mgr = result.managers["replay-ai6"]
    state = mgr.state
    assert state is not None

    line = mgr.status_line()
    card = mgr.status_card()
    assert state.status in line
    assert card.status == state.status


def test_replay_gate_passes_have_evidence_refs(trace, replay):
    result = replay(trace("ai6_completion_gate_conflict.jsonl"))
    decision = result.decisions[-1]
    passed = [g for g in decision["gate_results"] if g["status"] == "passed"]

    assert passed
    assert decision["evidence_refs"]
    assert all(g["evidence_refs"] or g["evidence"] for g in passed)
