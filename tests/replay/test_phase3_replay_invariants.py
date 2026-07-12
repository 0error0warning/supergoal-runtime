from __future__ import annotations

from supergoal_runtime.domain import (
    GoalEvent,
    GoalState,
    HypothesisRecord,
    ResearchFinding,
    SupergoalActionProposal,
)
from supergoal_runtime.evidence import evidence_ref_from_tool_call
from supergoal_runtime.gates import (
    apply_inertia_guard,
    gate_eligible_evidence_count,
    has_verified_execution_evidence,
    reconcile_done_evidence_gates,
    update_supergoal_gates,
)
from supergoal_runtime.projection import apply_events_to_state
from supergoal_runtime.store import SupergoalStore


def new_state(goal: str, *, goal_run_id: str = "gr_replay") -> GoalState:
    state = GoalState(goal=goal, goal_run_id=goal_run_id, mode="supergoal")
    update_supergoal_gates(state)
    return state


def project_tool_ref(state: GoalState, ref: object) -> bool:
    return apply_events_to_state(
        state,
        [
            GoalEvent(
                ts=0.0,
                type="tool_evidence_observed",
                turn=state.turns_used,
                summary=getattr(ref, "claim"),
                data={"evidence_ref": ref.to_dict()},
            )
        ],
        update_gates=update_supergoal_gates,
    )


def test_assistant_claims_do_not_pass_tool_backed_research_gate() -> None:
    state = new_state("research existing AI digest systems")
    state.inferred_user_intent = state.goal
    state.success_definition = "tool-backed research summary"
    state.research_findings.append(
        ResearchFinding(
            source_type="docs",
            title="claimed docs",
            locator="https://docs.example/claimed",
            claim="assistant says it checked docs",
            tool_call_id="assistant_turn",
            evidence_source="assistant_claim",
            trust_level="claim",
        )
    )

    update_supergoal_gates(state)

    g2 = next(gate for gate in state.gates if gate.id == "G2")
    assert g2.status != "passed"
    assert "tool-backed" in g2.reason or "provenance" in g2.reason


def test_assistant_prose_does_not_satisfy_g3_or_done_reconcile() -> None:
    state = new_state("produce a verified artifact")
    response = "Done. Created /tmp/a.md, pytest passed, verified artifact saved with evidence log."

    passed = reconcile_done_evidence_gates(state, response, "final artifact and tests reported")
    update_supergoal_gates(state)

    assert "G3" not in passed
    g3 = next(gate for gate in state.gates if gate.id == "G3")
    assert g3.status != "passed"
    assert "prose" in g3.reason or "tool" in g3.reason
    assert not has_verified_execution_evidence(state)


def test_blocked_tool_evidence_does_not_satisfy_artifact_gate() -> None:
    state = new_state("produce a verified artifact")
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

    changed = project_tool_ref(state, ref)

    assert changed is False
    assert not state.evidence
    assert not any(gate.id == "G3" and gate.status == "passed" for gate in state.gates)


def test_observed_tool_evidence_satisfies_g3() -> None:
    state = new_state("produce a verified artifact")
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

    changed = project_tool_ref(state, ref)

    assert changed is True
    assert state.evidence_layers.get("artifact")
    assert next(gate for gate in state.gates if gate.id == "G3").status == "passed"


def test_completion_reconciliation_allows_done_with_followups() -> None:
    state = new_state("write a small verified artifact")
    state.inferred_user_intent = "get a small artifact"
    state.success_definition = "artifact exists and is summarized"
    state.evidence_layers = {"artifact": ["/tmp/replay-small.md"]}
    state.last_verdict = "done"
    state.last_reason = "artifact satisfies request"

    reconcile_done_evidence_gates(
        state,
        "Done. Created /tmp/replay-small.md, verified artifact saved, evidence mapped to the request.",
        "artifact satisfies request",
    )
    update_supergoal_gates(state)

    blocking_open = [gate.id for gate in state.gates if gate.blocking and gate.status != "passed"]
    followups = [gate.id for gate in state.gates if not gate.blocking and gate.status == "followup"]
    assert blocking_open == []
    assert "G2" in followups
    assert next(gate for gate in state.gates if gate.id == "G3").status == "passed"
    assert next(gate for gate in state.gates if gate.id == "G4").status == "passed"


def test_compression_preserves_goal_run_id(tmp_path) -> None:
    store = SupergoalStore(db_path=tmp_path / "state.db")
    state = new_state("keep logical mission across compression", goal_run_id="gr_keep")
    store.import_run_bundle(
        state.goal_run_id,
        {"goal": state.goal, "goal_run_id": state.goal_run_id, "status": "active"},
        bindings=[("replay-compress-old", "set")],
        events=[],
    )

    rotated = store.rotate_session_binding(
        "replay-compress-old",
        "replay-compress-new",
        reason="compression",
    )

    assert rotated == "gr_keep"
    assert store.get_goal_run_id("replay-compress-new") == "gr_keep"
    assert store.load_run("gr_keep")["goal_run_id"] == "gr_keep"


def test_assistant_self_report_does_not_grow_gate_eligible_evidence() -> None:
    state = new_state("produce a verified artifact")
    state.action_proposal = SupergoalActionProposal(action_class="validation", target_gate_id="G3")
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = gate_eligible_evidence_count(state)
    before = gate_eligible_evidence_count(state)
    state.evidence.append("Verified with pytest, created /tmp/self-report.md, saved evidence log.")

    apply_inertia_guard(state)

    assert gate_eligible_evidence_count(state) == before
    assert state.same_action_no_evidence_count >= 1


def test_blocked_tool_call_does_not_reset_no_evidence_inertia() -> None:
    state = new_state("produce a verified artifact")
    state.action_proposal = SupergoalActionProposal(action_class="validation", target_gate_id="G3")
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = 0
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
    assert project_tool_ref(state, ref) is False

    apply_inertia_guard(state)

    assert gate_eligible_evidence_count(state) == 0
    assert state.same_action_no_evidence_count >= 2


def test_real_tool_evidence_resets_no_evidence_inertia() -> None:
    state = new_state("produce a verified artifact")
    state.action_proposal = SupergoalActionProposal(action_class="validation", target_gate_id="G3")
    state.current_action_class = "validation"
    state.action_history = ["validation"]
    state.last_action_evidence_count = 0
    state.same_action_no_evidence_count = 2
    ref = evidence_ref_from_tool_call(
        goal_run_id=state.goal_run_id,
        turn_id="turn-ok",
        tool_name="write_file",
        args={"path": "/tmp/ok.md"},
        result={"path": "/tmp/ok.md", "success": True},
        tool_call_id="tc-ok-inertia",
        status="ok",
    )
    assert ref is not None
    assert project_tool_ref(state, ref) is True

    apply_inertia_guard(state)

    assert gate_eligible_evidence_count(state) > 0
    assert state.same_action_no_evidence_count == 0


def test_infra_inertia_replans_to_first_strategy_gate() -> None:
    state = new_state("Bitget trading strategy with edge hypothesis")
    state.inferred_user_intent = state.goal
    state.success_definition = "find a strategy edge or produce no-edge attribution"
    state.research_sufficiency = "sufficient"
    state.evidence_layers = {"external_prior": ["github:strategy research"]}
    update_supergoal_gates(state)
    state.action_proposal = SupergoalActionProposal(
        action_class="infra_engineering",
        target_gate_id="SG-1",
        text="build another validator",
    )
    state.action_history = ["infra_engineering", "infra_engineering"]

    apply_inertia_guard(state)

    assert state.hard_gate_reason
    assert "infra_engineering" in state.hard_gate_reason
    assert state.should_replan is True
    assert state.replan_count > 0
    assert state.action_proposal.action_class == "hypothesis_generation"
    assert state.action_proposal.target_gate_id == "SG-1"


def test_trace_fixture_strategy_events_keep_goal_run_id_and_gate_pressure() -> None:
    state = new_state("Bitget trading strategy with edge hypothesis")
    for item in [
        HypothesisRecord(id="H1", claim="momentum edge", baseline="buy hold", experiment="backtest", kill_criteria="underperform", artifacts=["sha256:observed"], status="failed"),
        HypothesisRecord(id="H2", claim="mean reversion edge", baseline="buy hold", experiment="backtest", kill_criteria="underperform", artifacts=["sha256:observed"], status="failed"),
    ]:
        state.hypothesis_portfolio.append(item)
    apply_events_to_state(
        state,
        [GoalEvent(ts=0.0, type="hypothesis_failed", turn=1, summary="failed baseline", data={"category": "baseline_underperformance"})],
        update_gates=update_supergoal_gates,
    )
    state.action_proposal = SupergoalActionProposal(action_class="infra_engineering", target_gate_id="SG-4")

    apply_inertia_guard(state)

    first_open = next(gate for gate in state.gates if gate.status != "passed" and gate.blocking)
    assert state.goal_run_id == "gr_replay"
    assert state.action_proposal.target_gate_id == first_open.id or state.hard_gate_reason
