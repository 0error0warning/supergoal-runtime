"""Tests for hermes_cli/goals.py — persistent cross-turn goals."""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def hermes_home(tmp_path, monkeypatch):
    """Isolated HERMES_HOME so SessionDB.state_meta writes don't clobber the real one."""
    from pathlib import Path

    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))

    # Bust the goal-module's DB cache for each test so it re-resolves HERMES_HOME.
    from hermes_cli import goals

    goals._DB_CACHE.clear()
    yield home
    goals._DB_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────
# _parse_judge_response
# ──────────────────────────────────────────────────────────────────────


class TestParseJudgeResponse:
    def test_clean_json_done(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response('{"done": true, "reason": "all good"}')
        assert done is True
        assert reason == "all good"

    def test_clean_json_continue(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response('{"done": false, "reason": "more work needed"}')
        assert done is False
        assert reason == "more work needed"

    def test_json_in_markdown_fence(self):
        from hermes_cli.goals import _parse_judge_response

        raw = '```json\n{"done": true, "reason": "done"}\n```'
        done, reason, _ = _parse_judge_response(raw)
        assert done is True
        assert "done" in reason

    def test_json_embedded_in_prose(self):
        """Some models prefix reasoning before emitting JSON — we extract it."""
        from hermes_cli.goals import _parse_judge_response

        raw = 'Looking at this... the agent says X. Verdict: {"done": false, "reason": "partial"}'
        done, reason, _ = _parse_judge_response(raw)
        assert done is False
        assert reason == "partial"

    def test_string_done_values(self):
        from hermes_cli.goals import _parse_judge_response

        for s in ("true", "yes", "done", "1"):
            done, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert done is True
        for s in ("false", "no", "not yet"):
            done, _, _ = _parse_judge_response(f'{{"done": "{s}", "reason": "r"}}')
            assert done is False

    def test_malformed_json_fails_open(self):
        """Non-JSON → not done, with error-ish reason (so judge_goal can map to continue)."""
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response("this is not json at all")
        assert done is False
        assert reason  # non-empty

    def test_empty_response(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, _ = _parse_judge_response("")
        assert done is False
        assert reason


# ──────────────────────────────────────────────────────────────────────
# judge_goal — fail-open semantics
# ──────────────────────────────────────────────────────────────────────


class TestJudgeGoal:
    def test_empty_goal_skipped(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _ = judge_goal("", "some response")
        assert verdict == "skipped"

    def test_empty_response_continues(self):
        from hermes_cli.goals import judge_goal

        verdict, _, _ = judge_goal("ship the thing", "")
        assert verdict == "continue"

    def test_no_aux_client_continues(self):
        """Fail-open: if no aux client, we must return continue, not skipped/done."""
        from hermes_cli import goals

        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(None, None),
        ):
            verdict, _, _ = goals.judge_goal("my goal", "my response")
        assert verdict == "continue"

    def test_api_error_continues(self):
        """Judge exception → fail-open continue (don't wedge progress on judge bugs)."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("boom")
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _ = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert "judge error" in reason.lower()

    def test_judge_says_done(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": true, "reason": "achieved"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _ = goals.judge_goal("goal", "agent response")
        assert verdict == "done"
        assert reason == "achieved"

    def test_judge_says_continue(self):
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(content='{"done": false, "reason": "not yet"}')
                )
            ]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, reason, _ = goals.judge_goal("goal", "agent response")
        assert verdict == "continue"
        assert reason == "not yet"


# ──────────────────────────────────────────────────────────────────────
# GoalManager lifecycle + persistence
# ──────────────────────────────────────────────────────────────────────


class TestGoalManager:
    def test_no_goal_initial(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-1")
        assert mgr.state is None
        assert not mgr.is_active()
        assert not mgr.has_goal()
        assert "No active goal" in mgr.status_line()

    def test_set_then_status(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-2", default_max_turns=5)
        state = mgr.set("port the thing")
        assert state.goal == "port the thing"
        assert state.status == "active"
        assert state.max_turns == 5
        assert state.turns_used == 0
        assert mgr.is_active()
        assert "active" in mgr.status_line().lower()
        assert "port the thing" in mgr.status_line()

    def test_set_rejects_empty(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-3")
        with pytest.raises(ValueError):
            mgr.set("")
        with pytest.raises(ValueError):
            mgr.set("   ")

    def test_pause_and_resume(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-4")
        mgr.set("goal text")
        mgr.pause(reason="user-paused")
        assert mgr.state.status == "paused"
        assert not mgr.is_active()
        assert mgr.has_goal()

        mgr.resume()
        assert mgr.state.status == "active"
        assert mgr.is_active()

    def test_clear(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="test-sid-5")
        mgr.set("goal")
        mgr.clear()
        assert mgr.state is None
        assert not mgr.is_active()

    def test_persistence_across_managers(self, hermes_home):
        """Key invariant: a second manager on the same session sees the goal.

        This is what makes /resume work — each session rebinds its
        GoalManager and picks up the saved state.
        """
        from hermes_cli.goals import GoalManager

        mgr1 = GoalManager(session_id="persist-sid")
        mgr1.set("do the thing")

        mgr2 = GoalManager(session_id="persist-sid")
        assert mgr2.state is not None
        assert mgr2.state.goal == "do the thing"
        assert mgr2.is_active()

    def test_supergoal_mode_persists_and_uses_product_prompt(self, hermes_home):
        """/supergoal is a separate mode with a long-running-agent prompt.

        This guards the product behavior: normal /goal stays simple, while
        supergoal gets the higher-structure prompt for long autonomous work.
        """
        from hermes_cli.goals import GoalManager

        mgr1 = GoalManager(session_id="super-persist-sid")
        state = mgr1.set("ship the big project", max_turns=240, mode="supergoal")
        assert state.mode == "supergoal"
        assert "Supergoal" in mgr1.status_line()

        prompt = mgr1.next_continuation_prompt()
        assert prompt is not None
        assert "SUPERGOAL" in prompt
        assert "root intent" in prompt
        assert "first-principles" in prompt
        assert "existing tools" in prompt
        assert "Verify the result" in prompt
        assert "Supergoal State Board" in prompt
        assert "standing goal" not in prompt

        mgr2 = GoalManager(session_id="super-persist-sid")
        assert mgr2.state is not None
        assert mgr2.state.mode == "supergoal"
        assert mgr2.next_continuation_prompt() == prompt

    def test_supergoal_status_includes_diagnostics_after_progress(self, hermes_home):
        """Status should make it obvious whether a supergoal loop really advanced."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-status-sid")
        mgr.set("ship the big project", max_turns=240, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "needs more verification", False)):
            mgr.evaluate_after_turn("partial progress")
        mgr.record_continuation_enqueued(kind="gateway-fifo")

        line = GoalManager(session_id="super-status-sid").status_line()
        assert "Supergoal" in line
        assert "1/240 turns" in line
        assert "last=continue" in line
        assert "queued=" in line
        assert "kind=gateway-fifo" in line
        assert "step=" in line
        assert "events=" in line
        assert "needs more verification" in line

    def test_supergoal_compact_status_is_mobile_friendly(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-compact-status-sid")
        state = mgr.set("ship the big project", max_turns=240, mode="supergoal")
        state.turns_used = 3
        state.last_verdict = "continue"
        state.last_reason = "needs more verified work before this can be called complete"
        state.next_best_action = "satisfy the first open gate with concrete evidence"
        state.consecutive_critic_failures = 2

        line = mgr.status_line(compact=True)

        assert "Supergoal active · turns 3/240" in line
        assert "last continue: needs more verified work" in line
        assert "critic_failures 2" in line
        assert "next satisfy the first open gate" in line
        assert "Controls: /supergoal pause · /supergoal status · /supergoal clear" in line

    def test_supergoal_initializes_plan_and_event_log(self, hermes_home):
        """Product invariant: /supergoal starts with durable plan + audit events."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-plan-events-sid")
        state = mgr.set("ship the big project", max_turns=240, mode="supergoal")

        assert state.plan_steps
        assert state.current_step_id == "S1"
        assert state.plan_steps[0].status == "in_progress"
        assert "root intent" in state.plan_steps[0].title
        assert any("existing solutions" in step.title for step in state.plan_steps)
        events = mgr.recent_events(limit=10)
        assert [e.type for e in events] == ["goal_set", "plan_created"]
        reloaded = GoalManager(session_id="super-plan-events-sid")
        assert reloaded.state.event_count == 2
        assert reloaded.state.last_event_type == "plan_created"
        assert "plan_steps" in reloaded.state.render_supergoal_board()

    def test_goal_event_log_records_evaluation_and_replan_prompt(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-event-flow-sid")
        mgr.set("debug the flaky service", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "weak",
                 "strategy_health": "stuck",
                 "should_replan": True,
                 "next_best_action": "collect exact failing stack trace",
             }):
            decision = mgr.evaluate_after_turn("I checked logs but did not reproduce yet")

        assert "REPLAN REQUIRED" in decision["continuation_prompt"]
        events = mgr.recent_events(limit=20)
        event_types = [e.type for e in events]
        assert "critic" in event_types
        assert "turn_evaluated" in event_types
        assert "replan_prompted" in event_types
        assert GoalManager(session_id="super-event-flow-sid").state.last_event_type == "replan_prompted"

    def test_normal_goal_prompt_unchanged_by_supergoal(self, hermes_home):
        """Regression guard: adding /supergoal must not change /goal behavior."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="normal-goal-sid")
        mgr.set("do the normal thing")
        assert mgr.state.mode == "goal"
        prompt = mgr.next_continuation_prompt()
        assert prompt.startswith("[Continuing toward your standing goal]")
        assert "SUPERGOAL" not in prompt

    def test_supergoal_critic_updates_board_and_requests_replan(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-critic-sid")
        mgr.set("debug the flaky service", max_turns=20, mode="supergoal")
        critic_payload = {
            "inferred_user_intent": "Make the flaky service reliable with minimal operational risk",
            "success_definition": "A reproduced root cause and verified durable fix",
            "first_principles_model": ["A service is healthy only if dependencies are ready before traffic"],
            "existing_solution_scan": ["systemd ordering and readiness checks"],
            "research_findings": [
                {"source_type": "docs", "title": "systemd readiness docs", "locator": "man:systemd.service", "tool_call_id": "tc-docs", "evidence_quote_or_hash": "READY=1"},
                {"source_type": "github", "title": "service readiness examples", "locator": "https://github.com/example/readiness", "tool_call_id": "tc-gh", "evidence_quote_or_hash": "healthcheck pattern"},
                {"source_type": "benchmark", "title": "startup healthcheck trace", "locator": "/tmp/trace.log", "tool_call_id": "tc-bench", "evidence_quote_or_hash": "trace hash"},
            ],
            "build_vs_reuse_decision": "reuse: systemd readiness primitives before custom watchdogs",
            "literalism_risk": "low",
            "research_sufficiency": "sufficient",
            "progress": "weak",
            "strategy_health": "stuck",
            "root_cause_confidence": 0.35,
            "should_replan": True,
            "next_best_action": "Reproduce the failure with logs enabled",
            "new_hypotheses": ["race in startup ordering"],
            "new_evidence": ["health check fails before worker ready"],
            "missing_evidence": ["exact failing stack trace"],
        }
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value=critic_payload):
            decision = mgr.evaluate_after_turn("I checked logs but did not reproduce yet")

        assert decision["should_continue"] is True
        state = mgr.state
        assert state is not None
        assert state.progress == "weak"
        assert state.strategy_health == "stuck"
        assert state.inferred_user_intent.startswith("Make the flaky service")
        assert "dependencies are ready" in state.first_principles_model[0]
        assert "systemd ordering" in state.existing_solution_scan[0]
        assert state.build_vs_reuse_decision.startswith("reuse")
        assert state.literalism_risk == "low"
        assert state.research_sufficiency == "missing"  # critic claims are not tool-backed evidence
        assert state.should_replan is False  # consumed into the returned continuation prompt
        assert state.replan_count == 1
        assert "race in startup ordering" in state.hypotheses
        assert "health check fails before worker ready" in state.evidence
        assert any("exact failing stack trace" in r for r in state.risks)
        assert "Supergoal State Board" in decision["continuation_prompt"]
        assert "inferred_user_intent" in decision["continuation_prompt"]
        assert "first_principles_model" in decision["continuation_prompt"]
        assert "existing_solution_scan" in decision["continuation_prompt"]
        assert "REPLAN REQUIRED" in decision["continuation_prompt"]
        assert "Reproduce the failure" in decision["continuation_prompt"]

    def test_supergoal_critic_forces_replan_on_literalism_or_thin_research(self, hermes_home):
        """Supergoal must be more than long-running literal execution.

        If the critic sees the agent building before inferring root intent or
        scanning mature existing solutions, the next continuation must force a
        strategic replan even when ordinary progress appears real.
        """
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-literalism-replan-sid")
        mgr.set("connect my exchange API and monitor risk", max_turns=20, mode="supergoal")
        critic_payload = {
            "inferred_user_intent": "Continuously understand exchange account risk with low maintenance burden",
            "success_definition": "Reliable account/risk visibility using the shortest mature path",
            "first_principles_model": ["Monitoring needs correct data, alert thresholds, and delivery guarantees"],
            "existing_solution_scan": [],
            "build_vs_reuse_decision": "unknown: agent started bespoke implementation too early",
            "literalism_risk": "high",
            "research_sufficiency": "thin",
            "progress": "real",
            "strategy_health": "good",
            "should_replan": False,
            "next_best_action": "Scan mature Bitget/account monitoring options before writing more custom code",
        }
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value=critic_payload):
            decision = mgr.evaluate_after_turn("I started writing a custom monitor script")

        state = mgr.state
        assert state is not None
        assert state.literalism_risk == "high"
        assert state.research_sufficiency == "thin"
        assert state.replan_count == 1
        assert any("literalism risk" in r for r in state.risks)
        assert any("tool-backed research ledger is thin" in r for r in state.risks)
        assert "REPLAN REQUIRED" in decision["continuation_prompt"]
        assert "inferred root intent" in decision["continuation_prompt"]
        assert "reuse of existing solutions" in decision["continuation_prompt"]

    def test_supergoal_research_sufficiency_requires_source_diversity(self, hermes_home):
        """A critic cannot mark research sufficient without provenanced external findings."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-research-gate-sid")
        mgr.set("discover the best architecture", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "research_sufficiency": "sufficient",
                 "progress": "real",
                 "strategy_health": "good",
                 "research_findings": [
                     {"source_type": "paper", "title": "Relevant SOTA paper", "locator": "arxiv:1234.5678"},
                 ],
             }):
            decision = mgr.evaluate_after_turn("I found one paper but no implementation survey yet")

        assert mgr.state is not None
        assert mgr.state.research_sufficiency == "missing"
        assert mgr.state.research_findings[0].source_type == "paper"
        assert "REPLAN REQUIRED" in decision["continuation_prompt"]

    def test_supergoal_research_sufficiency_requires_tool_backed_provenance(self, hermes_home):
        """Critic-listed URLs are visible but cannot pass research gate without tool provenance."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-tool-backed-research-sid")
        mgr.set("discover the best architecture", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "research_sufficiency": "sufficient",
                 "progress": "real",
                 "strategy_health": "good",
                 "research_findings": [
                     {"source_type": "paper", "title": "Paper", "locator": "arxiv:1"},
                     {"source_type": "github", "title": "Repo", "locator": "https://github.com/x/y"},
                     {"source_type": "docs", "title": "Docs", "locator": "https://docs.example"},
                 ],
             }):
            decision = mgr.evaluate_after_turn("I listed sources from memory but did not call tools")

        assert mgr.state is not None
        assert mgr.state.research_sufficiency == "missing"
        assert "paper/critic" in decision["continuation_prompt"]
        assert "REPLAN REQUIRED" in decision["continuation_prompt"]

    def test_supergoal_legacy_tool_backed_research_keeps_provenance(self, hermes_home):
        from hermes_cli.goals import ResearchFinding

        finding = ResearchFinding.from_dict({
            "source_type": "docs",
            "title": "Legacy tool-backed source",
            "locator": "https://docs.example/legacy",
            "tool_call_id": "tc-legacy",
            "evidence_quote_or_hash": "sha256:abc",
        }, infer_legacy_tool_backed=True)

        assert finding is not None
        assert finding.evidence_source == "tool_call"
        assert finding.trust_level == "observed"
        assert finding.is_tool_backed is True

    def test_supergoal_tool_evidence_research_can_pass_gate(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager
        from hermes_cli.supergoal.evidence import record_tool_evidence

        mgr = GoalManager(session_id="super-tool-backed-pass-sid")
        mgr.set("discover the best architecture", max_turns=20, mode="supergoal")
        record_tool_evidence(
            session_id="super-tool-backed-pass-sid",
            tool_name="web_extract",
            args={"urls": ["https://arxiv.org/abs/1234.5678"]},
            result="paper quote about architecture",
            tool_call_id="tc-paper",
        )
        record_tool_evidence(
            session_id="super-tool-backed-pass-sid",
            tool_name="web_extract",
            args={"urls": ["https://github.com/x/y"]},
            result="github implementation details",
            tool_call_id="tc-gh",
        )
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "inferred_user_intent": "Pick a proven architecture",
                 "success_definition": "External evidence plus verified artifact",
                 "progress": "weak",
                 "strategy_health": "good",
             }):
            mgr.evaluate_after_turn("I used tools to inspect paper and repo")

        assert mgr.state is not None
        assert mgr.state.research_sufficiency == "sufficient"
        assert any(g.id == "G2" and g.status == "passed" for g in mgr.state.gates)

    def test_supergoal_strategy_gates_block_infra_inertia(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-inertia-guard-sid")
        mgr.set("产出 2-3 个交易策略假设并验证，没有 edge 就写 no-edge 报告", max_turns=20, mode="supergoal")
        assert any(g.id == "SG-1" for g in mgr.state.gates)
        for i in range(3):
            with patch.object(goals, "judge_goal", return_value=("continue", "subgoal unmet", False)), \
                 patch.object(goals, "critic_supergoal", return_value={
                     "progress": "real",
                     "strategy_health": "good",
                     "action_proposal": {
                         "action_class": "infra_engineering",
                         "target_gate_id": "SG-1",
                         "expected_evidence": ["validator script"],
                         "tools_needed": ["write_file"],
                         "max_turn_budget": 1,
                         "risk_level": "medium",
                         "why_this_gate_first": "mistaken infra first",
                         "stop_if": ["no hypotheses produced"],
                         "text": "add another validator checker module",
                     },
                 }):
                decision = mgr.evaluate_after_turn(f"turn {i}: I added another validator")

        assert mgr.state is not None
        assert mgr.state.hard_gate_reason
        assert "SG-1" in mgr.state.hard_gate_reason
        assert mgr.state.plan_steps[0].status == "in_progress"  # real progress alone cannot mark done
        assert mgr.state.action_proposal.action_class == "hypothesis_generation"
        assert mgr.state.action_proposal.target_gate_id == "SG-1"
        assert "HARD GATE / INERTIA GUARD" in decision["continuation_prompt"]
        assert "Generate a 3-item hypothesis portfolio" in decision["continuation_prompt"]

    def test_supergoal_action_proposal_target_must_match_first_blocking_gate(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-action-target-approval-sid")
        mgr.set("discover the best architecture", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "real",
                 "strategy_health": "good",
                 "action_proposal": {
                     "action_class": "validation",
                     "target_gate_id": "G3",
                     "expected_evidence": ["test output"],
                     "tools_needed": ["terminal"],
                     "max_turn_budget": 1,
                     "risk_level": "low",
                     "why_this_gate_first": "validate first",
                     "stop_if": ["tests fail"],
                     "text": "run validation report",
                 },
             }):
            decision = mgr.evaluate_after_turn("I will run a validation report")

        assert mgr.state is not None
        assert mgr.state.hard_gate_reason
        assert "targets G3" in mgr.state.hard_gate_reason
        assert "first failed blocking gate is G2" in mgr.state.hard_gate_reason
        assert "HARD GATE / INERTIA GUARD" in decision["continuation_prompt"]

    def test_supergoal_structured_action_proposal_not_overwritten_by_response_keywords(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-action-keyword-safe-sid")
        mgr.set("debug the mission", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "real",
                 "strategy_health": "good",
                 "action_proposal": {
                     "action_class": "validation",
                     "target_gate_id": "G1",
                     "expected_evidence": ["acceptance checklist"],
                     "tools_needed": ["read_file"],
                     "max_turn_budget": 1,
                     "risk_level": "low",
                     "why_this_gate_first": "G1 is first",
                     "stop_if": ["contract remains unclear"],
                     "text": "validate contract",
                 },
             }):
            mgr.evaluate_after_turn("This report mentions a script, validator, audit tool, and infra module.")

        assert mgr.state is not None
        assert mgr.state.current_action_class == "validation"
        assert mgr.state.action_proposal.action_class == "validation"

    def test_supergoal_action_proposal_target_uses_refreshed_gate_state(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager
        from hermes_cli.supergoal.evidence import record_tool_evidence

        mgr = GoalManager(session_id="super-action-refreshed-gate-sid")
        mgr.set("产出 2-3 个交易策略假设并验证，没有 edge 就写 no-edge 报告", max_turns=20, mode="supergoal")
        record_tool_evidence(
            session_id="super-action-refreshed-gate-sid",
            tool_name="web_extract",
            args={"urls": ["https://arxiv.org/abs/1"]},
            result="paper quote",
            tool_call_id="tc-paper",
        )
        record_tool_evidence(
            session_id="super-action-refreshed-gate-sid",
            tool_name="web_extract",
            args={"urls": ["https://github.com/example/repo"]},
            result="repo quote",
            tool_call_id="tc-gh",
        )
        portfolio = [
            {"id": "H1", "claim": "momentum", "baseline": "buy hold", "experiment": "backtest", "kill_criteria": "underperform", "status": "proposed", "artifacts": ["a.md"]},
            {"id": "H2", "claim": "mean reversion", "baseline": "buy hold", "experiment": "backtest", "kill_criteria": "underperform", "status": "proposed", "artifacts": ["b.md"]},
            {"id": "H3", "claim": "funding", "baseline": "buy hold", "experiment": "backtest", "kill_criteria": "underperform", "status": "proposed", "artifacts": ["c.md"]},
        ]
        with patch.object(goals, "judge_goal", return_value=("continue", "need experiments", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "real",
                 "strategy_health": "good",
                 "hypothesis_portfolio": portfolio,
                 "action_proposal": {
                     "action_class": "experiment_execution",
                     "expected_evidence": ["baseline", "experiment artifact", "verdict"],
                     "tools_needed": ["terminal"],
                     "text": "execute the proposed hypotheses",
                 },
             }):
            mgr.evaluate_after_turn("Created the 3-hypothesis portfolio; next run experiments.")

        assert mgr.state is not None
        assert next(g for g in mgr.state.gates if g.id == "SG-1").status == "passed"
        assert mgr.state.action_proposal.target_gate_id == "SG-2"
        assert "targets SG-1" not in (mgr.state.hard_gate_reason or "")

    def test_supergoal_hypothesis_portfolio_passes_strategy_gates(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-portfolio-gates-sid")
        mgr.set("产出 2-3 个交易策略假设并验证，没有 edge 就写 no-edge 报告", max_turns=20, mode="supergoal")
        portfolio = [
            {"id": "H1", "claim": "funding mean reversion", "baseline": "sma_trend", "experiment": "rolling backtest", "kill_criteria": "underperform baseline", "artifacts": ["reports/h1.json"], "status": "failed", "verdict_reason": "no edge"},
            {"id": "H2", "claim": "breakout after funding reset", "baseline": "sma_trend", "experiment": "rolling backtest", "kill_criteria": "fees erase edge", "artifacts": ["reports/h2.json"], "status": "failed", "verdict_reason": "fees erase"},
            {"id": "H3", "claim": "vol carry filter", "baseline": "sma_trend", "experiment": "rolling backtest", "kill_criteria": "drawdown too high", "artifacts": ["reports/h3.json"], "status": "killed", "verdict_reason": "risk too high"},
        ]
        with patch.object(goals, "judge_goal", return_value=("continue", "needs final report", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "inferred_user_intent": "Find trading edge or prove none",
                 "success_definition": "3 hypotheses tested against baseline with no-edge report",
                 "progress": "real",
                 "strategy_health": "good",
                 "current_action_class": "reporting",
                 "hypothesis_portfolio": portfolio,
                 "no_edge_report": "All tested hypotheses failed same-cost baseline after fees/funding.",
                 "research_findings": [
                     {"source_type": "paper", "title": "Market paper", "locator": "arxiv:1", "tool_call_id": "tc1", "evidence_quote_or_hash": "quote"},
                     {"source_type": "github", "title": "Backtest repo", "locator": "https://github.com/x/y", "tool_call_id": "tc2", "evidence_quote_or_hash": "quote"},
                 ],
             }):
            decision = mgr.evaluate_after_turn("I tested all hypotheses and wrote no-edge attribution")

        assert mgr.state is not None
        passed = {g.id for g in mgr.state.gates if g.status == "passed"}
        assert {"SG-1", "SG-2", "SG-3", "SG-4"}.issubset(passed)
        assert "hypothesis_portfolio" in decision["continuation_prompt"]

    def test_goal_state_migrates_across_compression_session_split(self, hermes_home):
        """Compression is a session-id rotation, not the end of a logical goal loop."""
        from hermes_cli.goals import GoalManager, migrate_goal_state

        old = GoalManager(session_id="super-old-sid")
        old.set("finish the autonomous mission", max_turns=240, mode="supergoal")
        assert old.state is not None
        old.state.turns_used = 7
        old.state.research_sufficiency = "thin"
        old.state.next_best_action = "survey external implementations"
        old._record_event("turn_evaluated", summary="needs external survey")

        migrated = migrate_goal_state("super-old-sid", "super-new-sid", reason="compression")

        assert migrated is not None
        assert migrated.status == "active"
        assert migrated.mode == "supergoal"
        assert migrated.turns_used == 7
        assert migrated.next_best_action == "survey external implementations"
        new_mgr = GoalManager(session_id="super-new-sid")
        assert new_mgr.is_active()
        prompt = new_mgr.next_continuation_prompt()
        assert prompt is not None
        assert "survey external implementations" in prompt
        old_state = GoalManager(session_id="super-old-sid").state
        assert old_state is not None
        assert old_state.status == "active"
        assert old_state.goal_run_id == new_mgr.state.goal_run_id
        assert migrated.goal_run_id == new_mgr.state.goal_run_id
        assert any(e.type == "session_rotated" for e in new_mgr.recent_events(limit=20))





    def test_supergoal_controller_decision_exposes_pipeline_and_typed_fields(self, hermes_home, monkeypatch):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-controller-pipeline-sid")
        mgr.set("ship via explicit pipeline", max_turns=5, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "needs evidence", False)) as judge_mock, \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "weak",
                 "strategy_health": "good",
                 "next_best_action": "collect verifier output",
             }) as critic_mock:
            decision = mgr.evaluate_after_turn("partial progress")

        assert decision["status"] == "active"  # legacy compatibility
        assert decision["control_status"] == "continue"
        assert decision["next_action"]["text"] == "collect verifier output"
        assert isinstance(decision["gate_results"], list)
        assert isinstance(decision["evidence_refs"], list)
        assert [p["phase"] for p in decision["pipeline"]] == [
            "observe", "project", "evaluate", "reconcile", "decide", "render"
        ]
        project_phase = next(p for p in decision["pipeline"] if p["phase"] == "project")
        assert project_phase["data"]["turns_used_after"] == 1
        evaluate_phase = next(p for p in decision["pipeline"] if p["phase"] == "evaluate")
        assert evaluate_phase["data"]["verdict"] == "continue"
        assert evaluate_phase["data"]["critic_applied"] is True
        judge_mock.assert_called_once()
        critic_mock.assert_called_once()

    def test_legacy_goal_events_migrate_to_goal_run_id(self, hermes_home):
        import json
        import time
        from hermes_cli.goals import GoalManager, GoalState, save_goal, load_goal_events
        from hermes_cli.goal_events import events_meta_key
        from hermes_cli.supergoal.store import get_session_db, legacy_goal_key

        legacy = GoalState(goal="legacy mission", mode="supergoal", status="active")
        db = get_session_db()
        assert db is not None
        db.set_meta(legacy_goal_key("legacy-events-sid"), legacy.to_json())
        db.set_meta(events_meta_key("legacy-events-sid"), json.dumps([
            {"ts": time.time(), "type": "verification_observed", "turn": 3, "summary": "legacy evidence", "data": {}}
        ], ensure_ascii=False))

        mgr = GoalManager(session_id="legacy-events-sid")

        assert mgr.state is not None
        assert mgr.state.goal_run_id.startswith("legacy-")
        events = load_goal_events("legacy-events-sid", limit=20)
        assert [e.type for e in events] == ["verification_observed"]
        assert mgr.state.event_count == 1

    def test_migrate_goal_state_preserves_existing_destination_goal(self, hermes_home):
        from hermes_cli.goals import GoalManager, migrate_goal_state

        old = GoalManager(session_id="migration-existing-old")
        old.set("old mission", max_turns=10, mode="supergoal")
        new = GoalManager(session_id="migration-existing-new")
        new.set("new mission", max_turns=10, mode="supergoal")
        assert old.state is not None and new.state is not None
        new_run_id = new.state.goal_run_id

        result = migrate_goal_state("migration-existing-old", "migration-existing-new", reason="compression")

        assert result is not None
        assert result.goal == "new mission"
        assert result.goal_run_id == new_run_id
        assert GoalManager(session_id="migration-existing-new").state.goal_run_id == new_run_id

    def test_supergoal_evaluate_uses_controller_facade(self, hermes_home, monkeypatch):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager
        from hermes_cli.supergoal.domain import ControllerDecision

        mgr = GoalManager(session_id="super-controller-facade-sid")
        mgr.set("ship via controller", max_turns=5, mode="supergoal")
        calls = []

        def fake_decide(self, ctx):
            calls.append((ctx.session_id, ctx.last_response, ctx.state.mode))
            return ControllerDecision(
                status="blocked",
                should_continue=False,
                next_action=None,
                continuation_prompt=None,
                gate_results=[],
                evidence_refs=[],
                verdict="continue",
                reason="controller seam",
                user_message="controller used",
                legacy_status="active",
            )

        monkeypatch.setattr("hermes_cli.supergoal.controller.SupergoalController.decide_after_turn", fake_decide)
        decision = mgr.evaluate_after_turn("latest response")

        assert calls == [("super-controller-facade-sid", "latest response", "supergoal")]
        assert decision["message"] == "controller used"

    def test_normal_goal_evaluate_skips_supergoal_controller(self, hermes_home, monkeypatch):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="normal-goal-controller-bypass-sid")
        mgr.set("ordinary goal", max_turns=5, mode="goal")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("normal /goal must not instantiate SupergoalController")

        monkeypatch.setattr("hermes_cli.supergoal.controller.SupergoalController.decide_after_turn", fail_if_called)
        with patch.object(goals, "judge_goal", return_value=("done", "ordinary done", False)):
            decision = mgr.evaluate_after_turn("done")

        assert decision["status"] == "done"
        assert decision["verdict"] == "done"

    def test_goal_run_id_is_stable_across_session_rotation(self, hermes_home):
        from hermes_cli.goals import GoalManager, append_goal_event, load_goal_events, migrate_goal_state

        old = GoalManager(session_id="logical-old-sid")
        old.set("finish the mission", max_turns=240, mode="supergoal")
        assert old.state is not None
        run_id = old.state.goal_run_id
        assert run_id
        append_goal_event("logical-old-sid", "verification_observed", summary="old evidence")

        migrated = migrate_goal_state("logical-old-sid", "logical-new-sid", reason="compression")

        new = GoalManager(session_id="logical-new-sid")
        assert migrated is not None
        assert new.state is not None
        assert new.state.goal_run_id == run_id
        assert GoalManager(session_id="logical-old-sid").state.goal_run_id == run_id
        event_types = [e.type for e in load_goal_events("logical-new-sid", limit=20)]
        assert "verification_observed" in event_types
        assert "session_rotated" in event_types

    def test_supergoal_periodic_replan_interval(self, hermes_home, monkeypatch):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager
        from hermes_cli.supergoal.evidence import record_tool_evidence

        mgr = GoalManager(session_id="super-periodic-replan-sid")
        mgr.set("ship a big feature", max_turns=20, mode="supergoal")
        record_tool_evidence(
            session_id="super-periodic-replan-sid",
            tool_name="web_extract",
            args={"urls": ["https://arxiv.org/abs/1"]},
            result="paper quote",
            tool_call_id="tc-paper",
        )
        record_tool_evidence(
            session_id="super-periodic-replan-sid",
            tool_name="web_extract",
            args={"urls": ["https://github.com/example/repo"]},
            result="repo quote",
            tool_call_id="tc-gh",
        )
        monkeypatch.setattr(goals, "_supergoal_replan_interval", lambda: 2)
        with patch.object(goals, "judge_goal", return_value=("continue", "keep going", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "real",
                 "strategy_health": "good",
             }):
            d1 = mgr.evaluate_after_turn("step 1")
            assert "REPLAN REQUIRED" not in d1["continuation_prompt"]
            d2 = mgr.evaluate_after_turn("step 2")
            assert "REPLAN REQUIRED" in d2["continuation_prompt"]
            assert mgr.state.replan_count == 1

    def test_supergoal_critic_prompt_json_schema_escapes_format_braces(self, hermes_home):
        """Regression: literal JSON schema braces must survive str.format()."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-critic-format-sid")
        state = mgr.set("diagnose the issue", max_turns=20, mode="supergoal")
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content='{"progress":"real","strategy_health":"good"}'))]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            data = goals.critic_supergoal(state, "I reproduced the failure with logs.")

        assert data is not None
        assert data["progress"] == "real"
        user_prompt = fake_client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        assert '"progress": "real|weak|none|regressed"' in user_prompt
        assert user_prompt.count("{") >= 1
        assert user_prompt.count("}") >= 1


    def test_supergoal_subgoal_adds_strategy_gates_incrementally(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-subgoal-gates-sid")
        mgr.set("improve the runtime", max_turns=20, mode="supergoal")
        assert not any(g.id == "SG-1" for g in mgr.state.gates)

        mgr.add_subgoal("必须产出 2-3 个交易策略假设并验证，没有 edge 就写 no-edge 报告")

        assert any(g.id == "SG-1" for g in mgr.state.gates)
        assert any(g.id == "SG-2" for g in mgr.state.gates)
        assert "gates=0/" in mgr.status_line()
        assert "first_gate=G1" in mgr.status_line()

    def test_supergoal_done_judge_cannot_bypass_open_blocking_gates(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-done-gate-override-sid")
        mgr.set("产出 2-3 个交易策略假设并验证，没有 edge 就写 no-edge 报告", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("done", "agent claimed finished", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "real",
                 "strategy_health": "good",
             }):
            decision = mgr.evaluate_after_turn("All done.")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert mgr.state.status == "active"
        assert "blocking supergoal gate G2 remains open" in decision["reason"]
        assert "REPLAN REQUIRED" in decision["continuation_prompt"]
        reconcile_phase = next(p for p in decision["pipeline"] if p["phase"] == "reconcile")
        gate_decision = reconcile_phase["data"]["gate_decision"]
        assert reconcile_phase["data"]["done_gate_precomputed"] is True
        assert gate_decision["gate_vetoed"] is True
        assert gate_decision["first_blocking_gate_id"] == "G2"
        assert decision["gate_decision"] == gate_decision
        assert mgr.state.last_verdict == "continue"
        assert "blocking supergoal gate G2 remains open" in (mgr.state.last_reason or "")
        g2 = next(g for g in mgr.state.gates if g.id == "G2")
        assert g2.kind == "domain_required"
        assert g2.blocking is True

    def test_supergoal_generic_research_gate_is_followup_not_acceptance(self, hermes_home):
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-generic-g2-followup-sid")
        mgr.set("finish school hub", max_turns=20, mode="supergoal")

        g2 = next(g for g in mgr.state.gates if g.id == "G2")
        assert g2.kind == "quality_followup"
        assert g2.blocking is False
        assert g2.status == "followup"

    def test_supergoal_done_with_final_evidence_reconciles_stale_generic_gates(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-done-evidence-reconcile-sid")
        mgr.set("finish school hub", max_turns=20, mode="supergoal")
        # Keep the research gate out of scope for this regression: the stale
        # live-loop failure was G1/G3/G4 remaining pending despite a final
        # evidenced report and a done judge verdict.
        for gate in mgr.state.gates:
            if gate.id == "G2":
                gate.status = "passed"
                gate.evidence = "pre-existing tool-backed research"
        mgr.state.evidence_layers = {"artifact": ["docs/school-hub-final.md"]}

        final_response = (
            "Completed the school hub mission.\n"
            "Changed: created the dashboard and saved docs/school-hub-final.md.\n"
            "Verified: pytest passed and the generated report was reviewed.\n"
            "Evidence: artifact docs/school-hub-final.md maps the result to the requested success criteria."
        )
        with patch.object(goals, "judge_goal", return_value=("done", "complete with verified artifacts", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn(final_response)

        assert decision["verdict"] == "done"
        assert decision["should_continue"] is False
        assert mgr.state.status == "done"
        passed = {g.id for g in mgr.state.gates if g.status == "passed"}
        assert {"G1", "G2", "G3", "G4"}.issubset(passed)
        assert mgr.state.consecutive_critic_failures == 1
        assert mgr.state.same_gate_stall_count == 0


    def test_supergoal_critic_failures_do_not_pause_when_board_progress_is_deterministic(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES

        mgr = GoalManager(session_id="super-critic-failure-counter-sid")
        mgr.set("debug the mission", max_turns=20, mode="supergoal")

        visible_progress = (
            "Research update: external web docs and benchmark evidence reviewed. "
            "Verified with pytest and saved artifact logs/release-check.log."
        )
        with patch.object(goals, "judge_goal", return_value=("continue", "still working", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decisions = [mgr.evaluate_after_turn(visible_progress) for _ in range(DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES)]

        assert decisions[-1]["should_continue"] is True
        assert decisions[-1]["status"] == "active"
        assert mgr.state is not None
        assert mgr.state.status == "active"
        assert mgr.state.consecutive_critic_failures == DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES
        assert mgr.state.evidence
        assert mgr.state.research_findings
        assert mgr.state.research_sufficiency == "missing"  # assistant-observed research is claim-level only

        mgr.resume(reset_budget=False)
        with patch.object(goals, "judge_goal", return_value=("continue", "still working", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "weak",
                 "strategy_health": "good",
             }):
            d_ok = mgr.evaluate_after_turn("partial with critic back")

        assert d_ok["should_continue"] is True
        assert mgr.state.consecutive_critic_failures == 0
        assert "critic_failures=" not in mgr.status_line()

    def test_supergoal_event_ledger_rehydrates_evidence_and_failure_taxonomy(self, hermes_home):
        from hermes_cli.goals import GoalManager

        sid = "super-event-ledger-rehydrate-sid"
        mgr = GoalManager(session_id=sid)
        state = mgr.set("find a profitable trading strategy", max_turns=20, mode="supergoal")
        state.evidence = []
        state.research_findings = []
        state.evidence_layers = {}
        state.failure_taxonomy = {}
        state.search_phase = "explore"
        state.admission_criteria = []

        mgr._record_event(
            "research_observed",
            summary="external paper and benchmark source checked",
            data={
                "source_type": "web",
                "title": "External strategy benchmark",
                "locator": "https://example.test/strategy",
                "claim": "candidate has public prior evidence",
                "retrieved_at": "2026-06-17T00:00:00+00:00",
                "tool_call_id": "tool-1",
                "evidence_source": "tool_call",
                "trust_level": "observed",
                "evidence_quote_or_hash": "quote",
            },
        )
        mgr._record_event(
            "artifact_observed",
            summary="/tmp/strategy_report.md",
            data={"artifact_path": "/tmp/strategy_report.md"},
        )
        for category in ["beta_exposure", "oos_instability", "drawdown_unacceptable"]:
            mgr._record_event(
                "hypothesis_failed",
                summary=f"failed due to {category}",
                data={"category": category},
            )

        reloaded = GoalManager(session_id=sid)
        assert reloaded.state is not None
        assert reloaded.state.evidence
        assert reloaded.state.research_findings
        assert reloaded.state.evidence_layers["external_prior"]
        assert "artifact" not in reloaded.state.evidence_layers
        assert reloaded.state.failure_taxonomy == {
            "beta_exposure": 1,
            "oos_instability": 1,
            "drawdown_unacceptable": 1,
        }
        assert reloaded.state.search_phase == "failure_taxonomy"
        assert any("independent information source" in c for c in reloaded.state.admission_criteria)
        prompt = reloaded.next_continuation_prompt()
        assert prompt is not None
        assert "FAILURE TAXONOMY PHASE" in prompt
        assert "DO NOT RUN ANOTHER ORDINARY BENCHMARK" in prompt
        assert "independent information source" in prompt

    def test_supergoal_g2_requires_tool_evidence_events_not_research_claims(self, hermes_home):
        from hermes_cli.goals import GoalManager
        from hermes_cli.supergoal.evidence import record_tool_evidence

        sid = "super-g2-event-layer-sid"
        mgr = GoalManager(session_id=sid)
        state = mgr.set("find a profitable trading strategy", max_turns=20, mode="supergoal")
        state.research_sufficiency = "sufficient"
        state.research_findings = []
        state.evidence_layers = {}
        for gate in state.gates:
            if gate.id == "G2":
                gate.status = "pending"
        # Assistant/critic claim events are allowed on the board but must not
        # satisfy tool-backed external provenance.
        mgr._record_event(
            "research_observed",
            summary="claimed external paper",
            data={
                "source_type": "paper",
                "title": "Claimed paper",
                "locator": "assistant_turn",
                "claim": "assistant claimed paper",
                "retrieved_at": "2026-06-17T00:00:00+00:00",
                "tool_call_id": "",
                "evidence_quote_or_hash": "quote",
                "evidence_source": "assistant_claim",
                "trust_level": "claim",
            },
        )

        claim_only = GoalManager(session_id=sid)
        assert next(g for g in claim_only.state.gates if g.id == "G2").status == "pending"

        record_tool_evidence(
            session_id=sid,
            tool_name="web_extract",
            args={"urls": ["https://arxiv.org/abs/1"]},
            result="paper quote",
            tool_call_id="tool-paper",
        )
        record_tool_evidence(
            session_id=sid,
            tool_name="web_extract",
            args={"urls": ["https://github.com/x/y"]},
            result="github quote",
            tool_call_id="tool-github",
        )
        with_external = GoalManager(session_id=sid)
        assert next(g for g in with_external.state.gates if g.id == "G2").status == "passed"

    def test_supergoal_records_observation_events_after_turn(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-observation-events-sid")
        mgr.set("find a profitable trading strategy", max_turns=20, mode="supergoal")
        response = (
            "Changed: ran benchmark and external news source scan. "
            "Verified with pytest. Artifact /tmp/alpha_report.md saved. "
            "Result failed rolling OOS and buy-hold baseline gate."
        )
        with patch.object(goals, "judge_goal", return_value=("continue", "not solved", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn(response)

        assert decision["should_continue"] is True
        pipeline = {entry["phase"]: entry for entry in decision["pipeline"]}
        assert pipeline["observe"]["data"]["observation_events"] >= 4
        assert pipeline["project"]["data"]["changed"] is True
        events = mgr.recent_events(limit=20)
        event_types = [event.type for event in events]
        assert {event.turn for event in events if event.type in {"artifact_observed", "verification_observed", "research_observed"}} == {1}
        assert "artifact_observed" in event_types
        assert "verification_observed" in event_types
        assert "research_observed" in event_types
        assert "hypothesis_failed" in event_types
        assert mgr.state is not None
        assert mgr.state.failure_taxonomy.get("baseline_underperformance") == 1
        assert "artifact" not in mgr.state.evidence_layers

    def test_supergoal_critic_uses_dedicated_route_and_budget(self, hermes_home, monkeypatch):
        from pathlib import Path
        from hermes_cli import goals

        Path(hermes_home / "config.yaml").write_text(
            "model:\n"
            "  provider: custom:CPA\n"
            "  default: gpt-5.5\n"
            "auxiliary:\n"
            "  supergoal_critic:\n"
            "    provider: custom:CPA\n"
            "    model: gpt-5.4-mini\n"
            "    max_tokens: 512\n"
            "    timeout: 7\n",
            encoding="utf-8",
        )
        state = goals.GoalState(goal="debug mission", mode="supergoal")

        captured = {}

        class FakeMessage:
            content = '{"progress":"weak","strategy_health":"good"}'

        class FakeCompletions:
            def create(self, **kwargs):
                captured.update(kwargs)
                return type("Resp", (), {"choices": [type("Choice", (), {"message": FakeMessage()})()]})()

        class FakeClient:
            chat = type("Chat", (), {"completions": FakeCompletions()})()

        def fake_get_text_auxiliary_client(task):
            captured["task"] = task
            return FakeClient(), "gpt-5.4-mini"

        monkeypatch.setattr("agent.auxiliary_client.get_text_auxiliary_client", fake_get_text_auxiliary_client)
        data = goals.critic_supergoal(state, "Verified: artifact report.md saved", timeout=30)

        assert data["progress"] == "weak"
        assert captured["task"] == "supergoal_critic"
        assert captured["model"] == "gpt-5.4-mini"
        assert captured["max_tokens"] == 512
        assert captured["timeout"] == 7

    def test_supergoal_blocked_done_verdict_is_partial_blocked_not_success(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-terminal-blocked-sid")
        state = mgr.set("把免费数据接满并跑完整量化评估", max_turns=240, mode="supergoal")
        state.evidence_layers = {"artifact": ["/tmp/partial_report.md"], "verification": ["pytest passed"]}
        for gate in state.gates:
            if gate.id == "G2":
                gate.status = "passed"
                gate.evidence = "external research checked"

        response = (
            "Partial report generated, but full free-data ingestion is blocked by live_forbidden. "
            "CoinGecko/DefiLlama/RSS remain pending; cannot continue until policy/workdir is fixed."
        )
        with patch.object(goals, "judge_goal", return_value=("done", "blocked by live_forbidden; partial report exists", False)), \
             patch.object(goals, "critic_supergoal", return_value={
                 "progress": "weak",
                 "strategy_health": "blocked",
                 "new_blockers": ["live_forbidden prevents remaining free-data ingestion"],
             }):
            decision = mgr.evaluate_after_turn(response)

        assert decision["should_continue"] is False
        assert decision["status"] == "paused"
        assert decision["control_status"] == "partial_blocked"
        assert decision["verdict"] == "done"
        assert "live_forbidden" in (mgr.state.paused_reason or "")
        assert mgr.state.status == "paused"
        card = mgr.status_card()
        assert card.status == "paused"
        assert card.level == "blocked"
        assert card.color == "red"

    def test_control_status_done_reason_with_blocker_is_not_done(self, hermes_home):
        from hermes_cli.supergoal.domain import ControllerDecision

        decision = ControllerDecision.from_dict({
            "status": "done",
            "verdict": "done",
            "reason": "live_forbidden prevented remaining data sources from being connected",
            "should_continue": False,
            "message": "Goal achieved: live_forbidden prevented completion",
        })

        assert decision.status == "partial_blocked"

    def test_control_status_policy_blocker_wording_is_not_done(self, hermes_home):
        from hermes_cli.supergoal.domain import ControllerDecision

        decision = ControllerDecision.from_dict({
            "status": "done",
            "verdict": "done",
            "reason": "blocked by policy before remaining data sources were connected",
            "should_continue": False,
            "message": "Goal achieved: blocked by policy",
        })

        assert decision.status == "partial_blocked"

    def test_control_status_historical_blocker_phrase_can_still_complete(self, hermes_home):
        from hermes_cli.supergoal.domain import ControllerDecision

        decision = ControllerDecision.from_dict({
            "status": "done",
            "verdict": "done",
            "reason": "completed after resolving the issue previously blocked by policy",
            "should_continue": False,
            "message": "Goal achieved: blocker resolved",
        })

        assert decision.status == "done"

    def test_control_status_resolved_permission_denied_can_still_complete(self, hermes_home):
        from hermes_cli.supergoal.domain import ControllerDecision

        decision = ControllerDecision.from_dict({
            "status": "done",
            "verdict": "done",
            "reason": "fixed the permission denied error and completed the task",
            "should_continue": False,
            "message": "Goal achieved: permissions fixed",
        })

        assert decision.status == "done"

    def test_control_status_active_permission_denied_is_partial_blocked(self, hermes_home):
        from hermes_cli.supergoal.domain import ControllerDecision

        decision = ControllerDecision.from_dict({
            "status": "done",
            "verdict": "done",
            "reason": "cannot continue because permission denied blocks the remaining writes",
            "should_continue": False,
            "message": "Goal achieved: blocked",
        })

        assert decision.status == "partial_blocked"

    def test_supergoal_critic_failures_pause_when_no_board_progress(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES

        mgr = GoalManager(session_id="super-critic-no-progress-sid")
        mgr.set("debug the mission", max_turns=20, mode="supergoal")

        with patch.object(goals, "judge_goal", return_value=("continue", "still working", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decisions = [mgr.evaluate_after_turn("partial") for _ in range(DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES)]

        assert decisions[-1]["should_continue"] is False
        assert decisions[-1]["status"] == "paused"
        assert mgr.state is not None
        assert mgr.state.status == "paused"
        assert mgr.state.consecutive_critic_failures == DEFAULT_MAX_CONSECUTIVE_CRITIC_FAILURES
        assert "critic/board update failed" in decisions[-1]["message"]

    def test_supergoal_deterministically_populates_g1_when_critic_fails(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-g1-fallback-sid")
        mgr.set("find a profitable trading strategy", max_turns=20, mode="supergoal")

        with patch.object(goals, "judge_goal", return_value=("continue", "still working", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn("I ran a baseline backtest and logged the result.")

        assert decision["should_continue"] is True
        assert mgr.state is not None
        assert mgr.state.inferred_user_intent == "find a profitable trading strategy"
        assert mgr.state.success_definition
        assert next(g for g in mgr.state.gates if g.id == "G1").status == "passed"

    def test_supergoal_records_assistant_claim_without_passing_g3(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-evidence-fallback-sid")
        mgr.set("verify the release", max_turns=20, mode="supergoal")

        response = "Verified with pytest tests/gateway/test_goal_verdict_send.py -q; artifact logs/release-check.log saved."
        with patch.object(goals, "judge_goal", return_value=("continue", "needs review", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn(response)

        assert decision["should_continue"] is True
        assert mgr.state is not None
        assert mgr.state.action_history
        assert mgr.state.evidence
        g3 = next(g for g in mgr.state.gates if g.id == "G3")
        assert g3.status != "passed"
        assert "tool" in g3.reason

    def test_supergoal_loaded_old_state_normalizes_g1_and_stale_stall(self, hermes_home):
        from hermes_cli.goals import GoalManager, save_goal

        sid = "super-loaded-normalize-sid"
        mgr = GoalManager(session_id=sid)
        state = mgr.set("debug a long-running mission", max_turns=20, mode="supergoal")
        state.inferred_user_intent = ""
        state.success_definition = ""
        state.last_failed_gate_id = "G1"
        state.same_gate_stall_count = 1
        state.last_reason = "completion judge said done, but supergoal gate G1 remains open: Intent contract captured"
        state.next_best_action = "Satisfy gate G1: Intent contract captured"
        save_goal(sid, state)

        reloaded = GoalManager(session_id=sid)

        assert reloaded.state is not None
        assert reloaded.state.inferred_user_intent == "debug a long-running mission"
        assert reloaded.state.success_definition
        assert next(g for g in reloaded.state.gates if g.id == "G1").status == "passed"
        assert reloaded.state.last_failed_gate_id == ""
        assert reloaded.state.same_gate_stall_count == 0
        assert "gate G1 remains open" not in (reloaded.state.last_reason or "")
        assert "Satisfy gate G1" not in reloaded.state.next_best_action

    def test_supergoal_repeated_same_gate_done_veto_pauses(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_SAME_GATE_STALLS

        mgr = GoalManager(session_id="super-same-gate-stall-sid")
        mgr.set("finish the mission", max_turns=20, mode="supergoal")

        with patch.object(goals, "judge_goal", return_value=("done", "agent claimed finished", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            for i in range(DEFAULT_MAX_SAME_GATE_STALLS - 1):
                decision = mgr.evaluate_after_turn(f"Done turn {i}.")
                assert decision["should_continue"] is True
                assert mgr.state.status == "active"

            decision = mgr.evaluate_after_turn("Done again.")

        assert decision["should_continue"] is False
        assert decision["status"] == "paused"
        assert mgr.state.status == "paused"
        assert mgr.state.last_failed_gate_id == "G3"
        assert mgr.state.same_gate_stall_count == DEFAULT_MAX_SAME_GATE_STALLS
        assert "gate G3 is stalled" in decision["message"]
        assert "stalled" in mgr.state.paused_reason


    def test_supergoal_done_with_only_followup_gate_open_does_not_stall(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-followup-gate-done-sid")
        mgr.set("finish school hub", max_turns=20, mode="supergoal")
        # Simulate a prior legacy done-veto on G2. For generic execution goals,
        # G2 is now a non-blocking follow-up, so final artifact evidence must
        # finish cleanly instead of pausing as stalled.
        mgr.state.last_failed_gate_id = "G2"
        mgr.state.same_gate_stall_count = 2
        mgr.state.evidence_layers = {"artifact": ["docs/school-hub-final.md"]}

        final_response = (
            "Completed the mission.\n"
            "Changed: saved docs/school-hub-final.md.\n"
            "Verified: pytest passed and the generated report was reviewed.\n"
            "Evidence: artifact docs/school-hub-final.md maps the result to the criteria."
        )
        with patch.object(goals, "judge_goal", return_value=("done", "complete with verified artifacts", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn(final_response)

        assert decision["should_continue"] is False
        assert decision["status"] == "done"
        assert mgr.state.status == "done"
        assert mgr.state.last_failed_gate_id == ""
        assert mgr.state.same_gate_stall_count == 0
        g2 = next(g for g in mgr.state.gates if g.id == "G2")
        assert g2.blocking is False
        assert g2.status in {"followup", "passed"}

    def test_supergoal_done_event_is_not_duplicated_after_completion(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-done-no-duplicate-event-sid")
        mgr.set("finish school hub", max_turns=20, mode="supergoal")
        mgr.state.evidence_layers = {"artifact": ["docs/school-hub-final.md"]}
        final_response = "Completed. Changed: docs/school-hub-final.md. Verified: pytest passed."
        with patch.object(goals, "judge_goal", return_value=("done", "complete", False)), \
             patch.object(goals, "critic_supergoal", return_value=None):
            decision = mgr.evaluate_after_turn(final_response)

        assert decision["status"] == "done"
        first_events = [e.type for e in mgr.recent_events(limit=20)]
        assert first_events.count("done") == 1

        second = mgr.evaluate_after_turn("late duplicate hook")
        assert second["verdict"] == "inactive"
        assert [e.type for e in mgr.recent_events(limit=20)].count("done") == 1

    def test_supergoal_continue_message_uses_supergoal_label(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="super-message-label-sid")
        mgr.set("ship a big thing", max_turns=20, mode="supergoal")
        with patch.object(goals, "judge_goal", return_value=("continue", "more work", False)), \
             patch.object(goals, "critic_supergoal", return_value={"progress": "weak", "strategy_health": "good"}):
            decision = mgr.evaluate_after_turn("partial")

        assert "Continuing toward supergoal" in decision["message"]

    def test_evaluate_after_turn_done(self, hermes_home):
        """Judge says done → status=done, no continuation."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-1")
        mgr.set("ship it")

        with patch.object(goals, "judge_goal", return_value=("done", "shipped", False)):
            decision = mgr.evaluate_after_turn("I shipped the feature.")

        assert decision["verdict"] == "done"
        assert decision["should_continue"] is False
        assert decision["continuation_prompt"] is None
        assert mgr.state.status == "done"
        assert mgr.state.turns_used == 1

    def test_evaluate_after_turn_continue_under_budget(self, hermes_home):
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-2", default_max_turns=5)
        mgr.set("a long goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "more work", False)):
            decision = mgr.evaluate_after_turn("made some progress")

        assert decision["verdict"] == "continue"
        assert decision["should_continue"] is True
        assert decision["continuation_prompt"] is not None
        assert "a long goal" in decision["continuation_prompt"]
        assert mgr.state.status == "active"
        assert mgr.state.turns_used == 1

    def test_evaluate_after_turn_budget_exhausted(self, hermes_home):
        """When turn budget hits ceiling, auto-pause instead of continuing."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-3", default_max_turns=2)
        mgr.set("hard goal")

        with patch.object(goals, "judge_goal", return_value=("continue", "not yet", False)):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.turns_used == 1
            assert mgr.state.status == "active"

            d2 = mgr.evaluate_after_turn("step 2")
            # turns_used is now 2 which equals max_turns → paused
            assert d2["should_continue"] is False
            assert mgr.state.status == "paused"
            assert mgr.state.turns_used == 2
            assert "budget" in (mgr.state.paused_reason or "").lower()

    def test_evaluate_after_turn_inactive(self, hermes_home):
        """evaluate_after_turn is a no-op when goal isn't active."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="eval-sid-4")
        d = mgr.evaluate_after_turn("anything")
        assert d["verdict"] == "inactive"
        assert d["should_continue"] is False

        mgr.set("a goal")
        mgr.pause()
        d2 = mgr.evaluate_after_turn("anything")
        assert d2["verdict"] == "inactive"
        assert d2["should_continue"] is False

    def test_continuation_prompt_shape(self, hermes_home):
        """The continuation prompt must include the goal text verbatim —
        and must be safe to inject as a user-role message (prompt-cache
        invariants: no system-prompt mutation)."""
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="cont-sid")
        mgr.set("port goal command to hermes")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "port goal command to hermes" in prompt
        assert prompt.strip()  # non-empty


# ──────────────────────────────────────────────────────────────────────
# Smoke: CommandDef is wired
# ──────────────────────────────────────────────────────────────────────


def test_goal_command_in_registry():
    from hermes_cli.commands import resolve_command

    cmd = resolve_command("goal")
    assert cmd is not None
    assert cmd.name == "goal"


def test_goal_command_dispatches_in_cli_registry_helpers():
    """goal shows up in autocomplete / help categories alongside other Session cmds."""
    from hermes_cli.commands import COMMANDS, COMMANDS_BY_CATEGORY

    assert "/goal" in COMMANDS
    session_cmds = COMMANDS_BY_CATEGORY.get("Session", {})
    assert "/goal" in session_cmds


# ──────────────────────────────────────────────────────────────────────
# Auto-pause on consecutive judge parse failures
# ──────────────────────────────────────────────────────────────────────


class TestJudgeParseFailureAutoPause:
    """Regression: weak judge models (e.g. deepseek-v4-flash) that return
    empty strings or non-JSON prose must auto-pause the loop after N turns
    instead of burning the whole turn budget."""

    def test_parse_response_flags_empty_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, parse_failed = _parse_judge_response("")
        assert done is False
        assert parse_failed is True
        assert "empty" in reason.lower()

    def test_parse_response_flags_non_json_as_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        done, reason, parse_failed = _parse_judge_response(
            "Let me analyze whether the goal is fully satisfied based on the agent's response..."
        )
        assert done is False
        assert parse_failed is True
        assert "not json" in reason.lower()

    def test_parse_response_clean_json_is_not_parse_failure(self):
        from hermes_cli.goals import _parse_judge_response

        done, _, parse_failed = _parse_judge_response(
            '{"done": false, "reason": "more work"}'
        )
        assert done is False
        assert parse_failed is False

    def test_api_error_does_not_count_as_parse_failure(self):
        """Transient network/API errors must not trip the auto-pause guard."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = RuntimeError("connection reset")
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, _, parse_failed = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert parse_failed is False

    def test_empty_judge_reply_flagged_as_parse_failure(self):
        """End-to-end: judge returns empty content → parse_failed=True."""
        from hermes_cli import goals

        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=""))]
        )
        with patch(
            "agent.auxiliary_client.get_text_auxiliary_client",
            return_value=(fake_client, "judge-model"),
        ):
            verdict, _, parse_failed = goals.judge_goal("goal", "response")
        assert verdict == "continue"
        assert parse_failed is True

    def test_auto_pause_after_three_consecutive_parse_failures(self, hermes_home):
        """N=3 consecutive parse failures → auto-pause with config pointer."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES

        assert DEFAULT_MAX_CONSECUTIVE_PARSE_FAILURES == 3
        mgr = GoalManager(session_id="parse-fail-sid-1", default_max_turns=20)
        mgr.set("do a thing")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge returned empty response", True)
        ):
            d1 = mgr.evaluate_after_turn("step 1")
            assert d1["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 1

            d2 = mgr.evaluate_after_turn("step 2")
            assert d2["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 2

            d3 = mgr.evaluate_after_turn("step 3")
            assert d3["should_continue"] is False
            assert d3["status"] == "paused"
            assert mgr.state.consecutive_parse_failures == 3
            # Message points at the config surface so the user can fix it.
            assert "auxiliary" in d3["message"]
            assert "goal_judge" in d3["message"]
            assert "config.yaml" in d3["message"]

    def test_parse_failure_counter_resets_on_good_reply(self, hermes_home):
        """A single good judge reply resets the counter — transient flakes don't pause."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-2", default_max_turns=20)
        mgr.set("another goal")

        # Two parse failures…
        with patch.object(
            goals, "judge_goal", return_value=("continue", "not json", True)
        ):
            mgr.evaluate_after_turn("step 1")
            mgr.evaluate_after_turn("step 2")
            assert mgr.state.consecutive_parse_failures == 2

        # …then one clean reply resets the counter.
        with patch.object(
            goals, "judge_goal", return_value=("continue", "making progress", False)
        ):
            d = mgr.evaluate_after_turn("step 3")
            assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0

    def test_parse_failure_counter_not_incremented_by_api_errors(self, hermes_home):
        """API/transport errors must NOT count toward the parse-failure auto-pause threshold."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="parse-fail-sid-3", default_max_turns=20)
        mgr.set("goal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge error: RuntimeError", False)
        ):
            # Use 4 iterations — under the API-failure threshold (5) so
            # the separate judge-API-failure auto-pause doesn't trigger.
            for _ in range(4):
                d = mgr.evaluate_after_turn("still going")
                assert d["should_continue"] is True
            assert mgr.state.consecutive_parse_failures == 0
            assert mgr.state.consecutive_judge_api_failures == 4
            assert mgr.state.status == "active"

    def test_judge_api_failure_auto_pause(self, hermes_home):
        """Supergoal must auto-pause after N consecutive judge API errors."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, DEFAULT_MAX_CONSECUTIVE_JUDGE_API_FAILURES

        mgr = GoalManager(session_id="api-fail-sid-1", default_max_turns=240)
        mgr.set("test supergoal", mode="supergoal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge error: InternalServerError", False)
        ):
            for i in range(DEFAULT_MAX_CONSECUTIVE_JUDGE_API_FAILURES - 1):
                d = mgr.evaluate_after_turn("turn")
                assert d["should_continue"] is True, f"should continue at turn {i+1}"
            # The next one should trigger auto-pause
            d = mgr.evaluate_after_turn("turn")
            assert d["should_continue"] is False
            assert d["status"] == "paused"
            assert "judge API failed" in mgr.state.paused_reason
            assert mgr.state.consecutive_judge_api_failures == DEFAULT_MAX_CONSECUTIVE_JUDGE_API_FAILURES

    def test_judge_api_failure_counter_resets_on_success(self, hermes_home):
        """A successful judge call must reset the API failure counter."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager

        mgr = GoalManager(session_id="api-fail-sid-2", default_max_turns=20)
        mgr.set("goal")

        # 3 API failures
        with patch.object(
            goals, "judge_goal", return_value=("continue", "judge error: RuntimeError", False)
        ):
            for _ in range(3):
                mgr.evaluate_after_turn("turn")
            assert mgr.state.consecutive_judge_api_failures == 3

        # 1 successful call — should reset counter
        with patch.object(
            goals, "judge_goal", return_value=("continue", "keep going", False)
        ):
            d = mgr.evaluate_after_turn("turn")
            assert d["should_continue"] is True
            assert mgr.state.consecutive_judge_api_failures == 0

    def test_consecutive_parse_failures_persists_across_goalmanager_reloads(
        self, hermes_home
    ):
        """The counter must be durable so cross-session resumes see it."""
        from hermes_cli import goals
        from hermes_cli.goals import GoalManager, load_goal

        mgr = GoalManager(session_id="parse-fail-sid-4", default_max_turns=20)
        mgr.set("persistent goal")

        with patch.object(
            goals, "judge_goal", return_value=("continue", "empty", True)
        ):
            mgr.evaluate_after_turn("r")
            mgr.evaluate_after_turn("r")

        reloaded = load_goal("parse-fail-sid-4")
        assert reloaded is not None
        assert reloaded.consecutive_parse_failures == 2


# ──────────────────────────────────────────────────────────────────────
# /subgoal — user-added criteria
# ──────────────────────────────────────────────────────────────────────


class TestGoalStateSubgoalsBackcompat:
    def test_old_state_meta_row_loads_without_subgoals(self):
        """A goal serialized BEFORE the subgoals field existed must
        round-trip with an empty list, not crash."""
        from hermes_cli.goals import GoalState

        legacy = json.dumps({
            "goal": "do a thing",
            "status": "active",
            "turns_used": 2,
            "max_turns": 20,
            "created_at": 1.0,
            "last_turn_at": 2.0,
            "consecutive_parse_failures": 0,
        })
        state = GoalState.from_json(legacy)
        assert state.goal == "do a thing"
        assert state.subgoals == []

    def test_subgoals_round_trip(self):
        from hermes_cli.goals import GoalState
        state = GoalState(goal="g", subgoals=["a", "b", "c"])
        rt = GoalState.from_json(state.to_json())
        assert rt.subgoals == ["a", "b", "c"]


class TestGoalManagerSubgoals:
    def test_add_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-add")
        mgr.set("main goal")
        text = mgr.add_subgoal("  use bullet points  ")
        assert text == "use bullet points"
        assert mgr.state.subgoals == ["use bullet points"]

    def test_add_subgoal_requires_active_goal(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-noactive")
        with pytest.raises(RuntimeError):
            mgr.add_subgoal("oops")

    def test_add_empty_subgoal_rejected(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-empty")
        mgr.set("g")
        with pytest.raises(ValueError):
            mgr.add_subgoal("   ")

    def test_remove_subgoal(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-remove")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")
        mgr.add_subgoal("third")
        removed = mgr.remove_subgoal(2)
        assert removed == "second"
        assert mgr.state.subgoals == ["first", "third"]

    def test_remove_subgoal_out_of_range(self, hermes_home):
        import pytest
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-oob")
        mgr.set("g")
        mgr.add_subgoal("only")
        with pytest.raises(IndexError):
            mgr.remove_subgoal(5)
        with pytest.raises(IndexError):
            mgr.remove_subgoal(0)

    def test_clear_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-clear")
        mgr.set("g")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        prev = mgr.clear_subgoals()
        assert prev == 2
        assert mgr.state.subgoals == []

    def test_subgoals_persist_across_reloads(self, hermes_home):
        """Subgoals stored in SessionDB survive a fresh GoalManager."""
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sub-persist")
        mgr.set("g")
        mgr.add_subgoal("first")
        mgr.add_subgoal("second")

        mgr2 = GoalManager(session_id="sub-persist")
        assert mgr2.state.subgoals == ["first", "second"]


class TestContinuationPromptWithSubgoals:
    def test_empty_subgoals_uses_original_template(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-empty")
        mgr.set("ship the feature")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" not in prompt

    def test_with_subgoals_includes_them(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="cp-with")
        mgr.set("ship the feature")
        mgr.add_subgoal("write tests")
        mgr.add_subgoal("update docs")
        prompt = mgr.next_continuation_prompt()
        assert prompt is not None
        assert "ship the feature" in prompt
        assert "Additional criteria" in prompt
        assert "1. write tests" in prompt
        assert "2. update docs" in prompt


class TestJudgeGoalWithSubgoals:
    def test_judge_uses_subgoals_template_when_provided(self, hermes_home):
        """judge_goal switches templates when subgoals is non-empty.

        We don't actually call the model — we patch the aux client to
        capture the prompt that would be sent.
        """
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "all done"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()

        with patch.object(goals, "get_text_auxiliary_client",
                          return_value=(_FakeClient, "fake-model"), create=True), \
             patch.object(goals, "get_auxiliary_extra_body",
                          return_value=None, create=True), \
             patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body",
                   return_value=None):
            verdict, reason, parse_failed = goals.judge_goal(
                "ship the feature",
                "ok shipped",
                subgoals=["write tests", "update docs"],
            )

        # The aux client was called with a prompt that includes the subgoals.
        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" in user_msg
        assert "1. write tests" in user_msg
        assert "2. update docs" in user_msg
        assert "every additional criterion" in user_msg
        assert verdict == "done"

    def test_judge_uses_original_template_when_no_subgoals(self, hermes_home):
        from unittest.mock import patch
        from hermes_cli import goals

        captured = {}

        class _FakeMsg:
            content = '{"done": true, "reason": "ok"}'
        class _FakeChoice:
            message = _FakeMsg()
        class _FakeResp:
            choices = [_FakeChoice()]
        class _FakeClient:
            class chat:
                class completions:
                    @staticmethod
                    def create(**kwargs):
                        captured.update(kwargs)
                        return _FakeResp()

        with patch("agent.auxiliary_client.get_text_auxiliary_client",
                   return_value=(_FakeClient, "fake-model")), \
             patch("agent.auxiliary_client.get_auxiliary_extra_body",
                   return_value=None):
            goals.judge_goal("ship it", "done", subgoals=None)

        sent_messages = captured.get("messages") or []
        user_msg = next((m["content"] for m in sent_messages if m["role"] == "user"), "")
        assert "Additional criteria" not in user_msg
        assert "ship it" in user_msg


class TestStatusLineSubgoalCount:
    def test_status_line_no_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-empty")
        mgr.set("ship it")
        line = mgr.status_line()
        assert "ship it" in line
        assert "subgoal" not in line.lower()

    def test_status_line_with_subgoals(self, hermes_home):
        from hermes_cli.goals import GoalManager
        mgr = GoalManager(session_id="sl-with")
        mgr.set("ship it")
        mgr.add_subgoal("a")
        mgr.add_subgoal("b")
        line = mgr.status_line()
        assert "2 subgoals" in line
