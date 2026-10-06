from __future__ import annotations

from dataclasses import asdict

import pytest

from supergoal_runtime.domain import (
    GoalContract,
    GoalEvent,
    GoalGate,
    GoalState,
    HypothesisRecord,
    SupergoalActionProposal,
)
from supergoal_runtime.evaluators import apply_supergoal_critic
from supergoal_runtime.gates import (
    default_supergoal_gate_specs,
    gate_eligible_evidence_count,
    update_supergoal_gates,
)
from supergoal_runtime.policy import ToolHookHandler
from supergoal_runtime.projection import apply_events_to_state
from supergoal_runtime.prompts import build_critic_messages
from supergoal_runtime.runtime import RuntimeManager
from supergoal_runtime.store import SupergoalStore


@pytest.mark.parametrize(
    "goal",
    [
        "Build an edge AI scheduler",
        "Write a deployment strategy",
        "Explain a scientific hypothesis",
        "Research storage systems",
        "Write a poem",
        "Find a trading strategy",
    ],
)
def test_task_keywords_never_select_a_domain_workflow(goal):
    specs = default_supergoal_gate_specs(goal)
    assert [asdict(spec) for spec in specs] == [
        asdict(spec) for spec in default_supergoal_gate_specs("")
    ]
    assert {spec.id for spec in specs if spec.blocking} == {"G1", "G4"}


def test_text_deliverable_completes_without_tools_or_external_sources(tmp_path):
    manager = RuntimeManager(
        store=SupergoalStore(db_path=tmp_path / "state.db"),
        judge=lambda *_a, **_k: ("done", "the requested poem was delivered", False),
    )
    manager.start("poem", "Write a four-line poem in Chinese")
    decision = manager.after_turn(
        "poem", final_response="春风拂柳绿，细雨润花红。\n溪水随云去，青山入梦中。"
    )
    state = manager.load_state_for_session("poem")
    assert decision["action"] == "done"
    assert state.status == "done"
    assert state.success_definition == state.goal
    assert not state.evidence_layers
    assert not any(gate.id.startswith("SG-") for gate in state.gates)


def test_explicit_artifact_and_verification_requirements_use_persisted_tool_events(
    tmp_path,
):
    seen_contracts = []

    def judge(*_args, **kwargs):
        seen_contracts.append(kwargs.get("contract"))
        return "done", "requested deliverable satisfies the goal", False

    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(store=store, judge=judge)
    contract = GoalContract(
        outcome="Create and test a report",
        evidence_requirements=["artifact", "verification"],
    )
    manager.start("report", "Create and test a report", contract=contract)
    # The caller cannot silently mutate persisted acceptance after start.
    contract.evidence_requirements.clear()
    decision = manager.after_turn("report", final_response="Task is complete.")
    assert decision["action"] == "continue"
    state = manager.load_state_for_session("report")
    assert state.contract.evidence_requirements == ["artifact", "verification"]
    assert next(g for g in state.gates if g.id == "G3").missing == [
        "artifact",
        "verification",
    ]

    hook = ToolHookHandler(store)
    hook.post_tool_call(
        session_id="report",
        tool_name="write_file",
        args={"path": "/tmp/report.md"},
        result={"path": "/tmp/report.md", "success": True},
        tool_call_id="write-report",
    )
    decision = manager.after_turn("report", final_response="Task is complete.")
    assert decision["action"] == "continue"
    assert next(
        g for g in manager.load_state_for_session("report").gates if g.id == "G3"
    ).missing == ["verification"]

    hook.post_tool_call(
        session_id="report",
        tool_name="terminal",
        args={"command": "pytest"},
        result={"output": "1 passed", "exit_code": 0},
        tool_call_id="test-report",
    )
    # No prose parser keyword: the ledger must still be projected.
    decision = manager.after_turn("report", final_response="Task is complete.")
    assert decision["action"] == "done"
    state = manager.load_state_for_session("report")
    assert next(g for g in state.gates if g.id == "G3").status == "passed"
    assert all(
        c.evidence_requirements == ["artifact", "verification"] for c in seen_contracts
    )


def test_assistant_and_critic_cannot_forge_required_proof_or_change_acceptance(
    tmp_path,
):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(
        store=store,
        judge=lambda *_a, **_k: ("done", "agent says it is finished", False),
        critic=lambda *_a, **_k: {
            "progress": "real",
            "plan_health": "good",
            "success_definition": "three hypotheses and a baseline",
            "contract": {"evidence_requirements": []},
            "no_edge_report": "no edge found",
            "hypothesis_portfolio": [
                {"id": "H1", "claim": "invented claim", "status": "passed"}
            ],
            "research_findings": [{"source_type": "web", "title": "fake source"}],
        },
    )
    manager.start(
        "proof", "Write a report file", contract={"evidence_requirements": ["artifact"]}
    )
    decision = manager.after_turn(
        "proof",
        final_response="Created /tmp/report.md. Verified with pytest, tests passed.",
    )
    state = manager.load_state_for_session("proof")
    assert decision["action"] == "continue"
    assert state.success_definition == "Write a report file"
    assert state.contract.evidence_requirements == ["artifact"]
    assert not state.hypothesis_portfolio and not state.no_edge_report
    assert gate_eligible_evidence_count(state) == 0
    assert next(g for g in state.gates if g.id == "G3").status == "pending"


def test_single_requested_external_source_needs_no_global_research_quota(tmp_path):
    store = SupergoalStore(db_path=tmp_path / "state.db")
    manager = RuntimeManager(
        store=store,
        judge=lambda *_a, **_k: ("done", "one requested source summarized", False),
    )
    manager.start(
        "source",
        "Summarize one documentation page",
        contract={"evidence_requirements": ["external_source"]},
    )
    ToolHookHandler(store).post_tool_call(
        session_id="source",
        tool_name="web_search",
        args={"query": "documentation"},
        result={"url": "https://example.org/docs", "text": "Documentation content"},
        tool_call_id="source-read",
    )
    decision = manager.after_turn(
        "source", final_response="Here is the requested summary."
    )
    assert decision["action"] == "done"


@pytest.mark.parametrize("requirements", ["artifact", None, ["baseline"], [{}], [1]])
def test_invalid_evidence_requirements_fail_explicitly(requirements):
    with pytest.raises(ValueError):
        GoalContract.from_dict({"evidence_requirements": requirements})


def test_legacy_policy_gates_are_archived_once_and_do_not_return_after_reload():
    state = GoalState(
        goal="Build an edge AI scheduler",
        mode="supergoal",
        inferred_user_intent="Build an edge AI scheduler",
        success_definition="legacy rule requires a no-edge report",
        gates=[
            GoalGate("SG-1", "three hypotheses"),
            GoalGate("SG-2", "baseline experiment"),
        ],
        action_proposal=SupergoalActionProposal(
            target_gate_id="SG-1", text="invent hypotheses"
        ),
        next_best_action="invent hypotheses",
        hard_gate_reason="strategy gates unmet",
        hypothesis_portfolio=[
            HypothesisRecord(id="H1", claim="legacy claim", status="passed")
        ],
        no_edge_report="legacy no-edge report",
        evidence_layers={"verified_hypothesis": ["legacy claim"]},
    )
    update_supergoal_gates(state)
    restored = GoalState.from_json(state.to_json())
    update_supergoal_gates(restored)
    update_supergoal_gates(restored)
    assert {gate.id for gate in restored.retired_gates} == {"SG-1", "SG-2"}
    assert len(restored.retired_gates) == 2
    assert not any(gate.id.startswith("SG-") for gate in restored.gates)
    assert not restored.next_best_action and not restored.hard_gate_reason
    assert restored.hypothesis_portfolio[0].claim == "legacy claim"
    assert restored.hypothesis_portfolio[0].status == "passed"
    assert restored.no_edge_report == "legacy no-edge report"
    assert gate_eligible_evidence_count(restored) == 0
    assert next(g for g in restored.gates if g.id == "G4").status != "passed"
    prompt = str(
        build_critic_messages(restored, "Continuing the requested scheduler work.")
    )
    assert "three hypotheses" not in prompt and "no-edge" not in prompt
    assert "verified_hypothesis" not in prompt


def test_no_research_is_not_a_reason_to_replan_a_text_task():
    state = GoalState(
        goal="Write a poem",
        mode="supergoal",
        inferred_user_intent="Write a poem",
        success_definition="Write a poem",
    )
    apply_supergoal_critic(
        state,
        {
            "progress": "real",
            "plan_health": "good",
            "action_proposal": {"text": "finish the poem"},
        },
    )
    assert not state.should_replan
    assert not state.hard_gate_reason
    assert state.same_action_no_evidence_count == 0


@pytest.mark.parametrize(
    "source,expected", [("human_input", "passed"), ("assistant_claim", "pending")]
)
def test_explicit_human_acceptance_cannot_be_replaced_by_an_assistant_claim(
    source, expected
):
    state = GoalState(
        goal="Present a design for approval",
        mode="supergoal",
        contract=GoalContract(evidence_requirements=["human_acceptance"]),
    )
    apply_events_to_state(
        state,
        [
            GoalEvent(
                ts=0,
                type="tool_evidence_observed",
                turn=1,
                summary="approval",
                data={
                    "evidence_ref": {
                        "id": "ev-approval",
                        "goal_run_id": state.goal_run_id,
                        "source": source,
                        "claim": "design accepted",
                        "trust_level": "verified",
                    }
                },
            )
        ],
        update_gates=update_supergoal_gates,
    )
    assert next(g for g in state.gates if g.id == "G3").status == expected


def test_explicit_contract_is_in_the_durable_start_prompt(tmp_path):
    manager = RuntimeManager(store=SupergoalStore(db_path=tmp_path / "state.db"))
    state, continuation = manager.start_for_command(
        "contract",
        "Create a report",
        contract={"constraints": "Use Chinese", "evidence_requirements": ["artifact"]},
    )
    assert "Use Chinese" in continuation["prompt"]
    assert "required_evidence: artifact" in continuation["prompt"]
    assert state.contract.outcome == "Create a report"
