from unittest.mock import patch


def test_replay_assistant_claims_do_not_pass_tool_backed_research_gate(_isolate_hermes_home):
    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_id="replay-claim-only")
    mgr.set("research existing AI digest systems", max_turns=10, mode="supergoal")
    with patch.object(goals, "judge_goal", return_value=("continue", "needs tool-backed sources", False)), patch.object(
        goals,
        "critic_supergoal",
        return_value={
            "progress": "real",
            "strategy_health": "good",
            "research_findings": [
                {
                    "source_type": "docs",
                    "title": "claimed docs",
                    "locator": "https://docs.example/claimed",
                    "claim": "assistant says it checked docs",
                    "tool_call_id": "assistant_turn",
                    "evidence_source": "assistant_claim",
                    "trust_level": "claim",
                }
            ],
            "action_proposal": {
                "action_class": "research",
                "target_gate_id": "G2",
                "expected_evidence": ["tool-backed source"],
                "tools_needed": ["web_extract"],
                "text": "collect real source evidence",
            },
        },
    ):
        decision = mgr.evaluate_after_turn("I checked docs and web sources in prose only.")

    assert mgr.state is not None
    g2 = next(g for g in mgr.state.gates if g.id == "G2")
    assert g2.status != "passed"
    assert "tool-backed" in (g2.reason or "") or "missing" in (g2.reason or "")
    assert decision["should_continue"] is True


def test_replay_blocked_tool_evidence_does_not_satisfy_artifact_gate(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, GoalEvent, _goal_events_state_changed
    from hermes_cli.supergoal.evidence import evidence_ref_from_tool_call

    mgr = GoalManager(session_id="replay-blocked-evidence")
    state = mgr.set("produce a verified artifact", mode="supergoal")
    ref = evidence_ref_from_tool_call(
        goal_run_id=state.goal_run_id,
        turn_id="turn-blocked",
        tool_name="write_file",
        args={"path": "/tmp/blocked.md", "content": "x"},
        result={"error": "Supergoal policy deny: path outside allowlist"},
        tool_call_id="tc-blocked",
        status="blocked",
    )
    assert ref is not None
    changed = _goal_events_state_changed(
        state,
        [
            GoalEvent(
                ts=0.0,
                type="tool_evidence_observed",
                turn=0,
                summary=ref.claim,
                data={"evidence_ref": ref.to_dict()},
            )
        ],
    )

    assert changed is False
    assert not state.evidence
    assert not any(g.id == "G3" and g.status == "passed" for g in state.gates)


def test_replay_assistant_prose_does_not_satisfy_g3_or_done_reconcile(_isolate_hermes_home):
    from hermes_cli import goals
    from hermes_cli.goals import GoalManager

    mgr = GoalManager(session_id="replay-prose-g3")
    mgr.set("produce a verified artifact", mode="supergoal")
    response = "Done. Created /tmp/a.md, pytest passed, verified artifact saved with evidence log."
    with patch.object(goals, "judge_goal", return_value=("done", "final artifact and tests reported", True)), patch.object(
        goals,
        "critic_supergoal",
        return_value=None,
    ):
        decision = mgr.evaluate_after_turn(response)

    assert mgr.state is not None
    g3 = next(g for g in mgr.state.gates if g.id == "G3")
    assert g3.status != "passed"
    assert "prose" in g3.reason or "tool" in g3.reason
    assert decision["should_continue"] is True


def test_replay_observed_tool_evidence_satisfies_g3(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, GoalEvent, _goal_events_state_changed
    from hermes_cli.supergoal.evidence import evidence_ref_from_tool_call

    mgr = GoalManager(session_id="replay-observed-evidence")
    state = mgr.set("produce a verified artifact", mode="supergoal")
    ref = evidence_ref_from_tool_call(
        goal_run_id=state.goal_run_id,
        turn_id="turn-ok",
        tool_name="write_file",
        args={"path": "/tmp/ok.md", "content": "x"},
        result={"path": "/tmp/ok.md", "success": True},
        tool_call_id="tc-ok",
        status="ok",
    )
    assert ref is not None
    changed = _goal_events_state_changed(
        state,
        [
            GoalEvent(
                ts=0.0,
                type="tool_evidence_observed",
                turn=0,
                summary=ref.claim,
                data={"evidence_ref": ref.to_dict()},
            )
        ],
    )

    assert changed is True
    assert state.evidence_layers.get("artifact")
    assert next(g for g in state.gates if g.id == "G3").status == "passed"


def test_replay_every_policy_checked_tool_call_has_decision(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, save_goal
    from hermes_cli.supergoal.policy import PermissionContract, PolicyGuard

    mgr = GoalManager(session_id="replay-policy")
    state = mgr.set("safe unattended run", mode="supergoal")
    state.permission_mode = "supervised"
    state.permission_contract = {
        "filesystem_allowlist": ["/tmp/allowed"],
        "network_allowlist": ["allowed.example"],
        "destructive_actions": "allow",
        "trading_mode": "live_forbidden",
    }
    save_goal("replay-policy", state)
    contract = PermissionContract.from_mapping(state.permission_contract)

    calls = [
        ("terminal", {"command": "curl https://unapproved.example/data"}),
        ("write_file", {"path": "/tmp/allowed/out.txt", "content": "ok"}),
        ("patch", {"mode": "patch", "patch": "*** Begin Patch\n*** Update File: /tmp/allowed/out.txt\n@@\n-old\n+new\n*** End Patch"}),
    ]
    decisions = [
        PolicyGuard.pre_tool_call(state.goal_run_id, state.action_proposal, tool, args, contract, mode=state.permission_mode)
        for tool, args in calls
    ]

    assert len(decisions) == len(calls)
    assert all(d.decision in {"allow", "deny", "require_user_approval", "sandbox_only"} for d in decisions)
    assert decisions[0].decision == "deny"
    assert decisions[1].decision == "allow"
    assert decisions[2].decision == "allow"


def test_replay_assistant_self_report_does_not_grow_gate_eligible_evidence(_isolate_hermes_home):
    from hermes_cli.goals import (
        GoalManager,
        _apply_inertia_guard,
        _gate_eligible_evidence_count,
        _record_supergoal_turn_artifacts,
    )

    mgr = GoalManager(session_id="replay-self-report-no-growth")
    state = mgr.set("produce a verified artifact", mode="supergoal")
    state.action_proposal.action_class = "validation"
    state.action_proposal.target_gate_id = "G3"
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = _gate_eligible_evidence_count(state)
    before = _gate_eligible_evidence_count(state)

    response = "Verified with pytest, created /tmp/self-report.md, saved report and evidence log."
    changed = _record_supergoal_turn_artifacts(state, response)
    _apply_inertia_guard(state)

    assert changed is True
    assert state.evidence  # board hint retained
    assert _gate_eligible_evidence_count(state) == before
    assert state.same_action_no_evidence_count >= 1


def test_replay_blocked_tool_call_does_not_reset_no_evidence_inertia(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, GoalEvent, _apply_inertia_guard, _gate_eligible_evidence_count
    from hermes_cli.supergoal.evidence import evidence_ref_from_tool_call

    mgr = GoalManager(session_id="replay-blocked-no-reset")
    state = mgr.set("produce a verified artifact", mode="supergoal")
    state.action_proposal.action_class = "validation"
    state.action_proposal.target_gate_id = "G3"
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = _gate_eligible_evidence_count(state)
    state.same_action_no_evidence_count = 1

    ref = evidence_ref_from_tool_call(
        goal_run_id=state.goal_run_id,
        turn_id="turn-blocked",
        tool_name="write_file",
        args={"path": "/tmp/blocked.md", "content": "x"},
        result={"error": "blocked"},
        tool_call_id="tc-blocked-inertia",
        status="blocked",
    )
    assert ref is not None
    from hermes_cli.goals import _goal_events_state_changed
    goals_changed = _goal_events_state_changed(
        state,
        [GoalEvent(ts=0.0, type="tool_evidence_observed", turn=0, summary=ref.claim, data={"evidence_ref": ref.to_dict()})],
    )
    assert goals_changed is False

    _apply_inertia_guard(state)

    assert _gate_eligible_evidence_count(state) == 0
    assert state.same_action_no_evidence_count >= 2


def test_replay_real_tool_evidence_resets_no_evidence_inertia(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, GoalEvent, _apply_inertia_guard, _gate_eligible_evidence_count, _goal_events_state_changed
    from hermes_cli.supergoal.evidence import evidence_ref_from_tool_call

    mgr = GoalManager(session_id="replay-real-evidence-reset")
    state = mgr.set("produce a verified artifact", mode="supergoal")
    state.action_proposal.action_class = "validation"
    state.action_proposal.target_gate_id = "G3"
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = 0
    state.same_action_no_evidence_count = 2

    ref = evidence_ref_from_tool_call(
        goal_run_id=state.goal_run_id,
        turn_id="turn-ok",
        tool_name="write_file",
        args={"path": "/tmp/ok.md", "content": "x"},
        result={"path": "/tmp/ok.md", "success": True},
        tool_call_id="tc-ok-inertia",
        status="ok",
    )
    assert ref is not None
    changed = _goal_events_state_changed(
        state,
        [GoalEvent(ts=0.0, type="tool_evidence_observed", turn=0, summary=ref.claim, data={"evidence_ref": ref.to_dict()})],
    )
    assert changed is True

    _apply_inertia_guard(state)

    assert _gate_eligible_evidence_count(state) > 0
    assert state.same_action_no_evidence_count == 0
    assert next(g for g in state.gates if g.id == "G3").status == "passed"


def test_replay_assistant_research_claim_does_not_grow_gate_eligible_evidence(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, GoalEvent, _gate_eligible_evidence_count, _goal_events_state_changed

    mgr = GoalManager(session_id="replay-research-claim-no-growth")
    state = mgr.set("research a strategy", mode="supergoal")
    before = _gate_eligible_evidence_count(state)
    changed = _goal_events_state_changed(
        state,
        [
            GoalEvent(
                ts=0.0,
                type="research_observed",
                turn=0,
                summary="assistant claims external docs checked",
                data={
                    "source_type": "web",
                    "title": "claimed docs",
                    "locator": "assistant_turn",
                    "claim": "assistant claims docs checked",
                    "tool_call_id": "",
                    "evidence_source": "assistant_claim",
                    "trust_level": "claim",
                    "evidence_quote_or_hash": "claimed quote",
                },
            )
        ],
    )

    assert changed is True
    assert state.research_findings
    assert not state.evidence_layers.get("external_prior")
    assert _gate_eligible_evidence_count(state) == before


def test_replay_critic_hypothesis_artifact_claim_does_not_satisfy_g3(_isolate_hermes_home):
    from hermes_cli import goals
    from hermes_cli.goals import GoalManager, _gate_eligible_evidence_count

    mgr = GoalManager(session_id="replay-critic-hypothesis-claim")
    mgr.set("test a strategy hypothesis", mode="supergoal")
    with patch.object(goals, "judge_goal", return_value=("continue", "needs verifier", False)), patch.object(
        goals,
        "critic_supergoal",
        return_value={
            "progress": "real",
            "strategy_health": "good",
            "hypothesis_portfolio": [
                {
                    "id": "H1",
                    "claim": "critic says artifact exists",
                    "baseline": "baseline",
                    "experiment": "experiment",
                    "kill_criteria": "kill",
                    "status": "passed",
                    "artifacts": ["/tmp/critic-claimed.md"],
                    "verdict_reason": "critic self-report only",
                }
            ],
        },
    ):
        mgr.evaluate_after_turn("critic will claim hypothesis artifact")

    assert mgr.state is not None
    assert mgr.state.hypothesis_portfolio
    assert _gate_eligible_evidence_count(mgr.state) == 0
    assert next(g for g in mgr.state.gates if g.id == "G3").status != "passed"


def test_replay_verified_hypothesis_artifact_counts_for_g3(_isolate_hermes_home):
    from hermes_cli.goals import GoalManager, HypothesisRecord, _gate_eligible_evidence_count, _update_supergoal_gates

    mgr = GoalManager(session_id="replay-verified-hypothesis")
    state = mgr.set("test a strategy hypothesis", mode="supergoal")
    state.hypothesis_portfolio = [
        HypothesisRecord(
            id="H1",
            claim="verified artifact",
            baseline="baseline",
            experiment="experiment",
            kill_criteria="kill",
            status="passed",
            artifacts=["tool_evidence:/tmp/verified.md"],
            verdict_reason="verified by tool_evidence_observed sha256:abc",
        )
    ]
    _update_supergoal_gates(state)

    assert _gate_eligible_evidence_count(state) == 1
    assert next(g for g in state.gates if g.id == "G3").status == "passed"
