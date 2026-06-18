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
