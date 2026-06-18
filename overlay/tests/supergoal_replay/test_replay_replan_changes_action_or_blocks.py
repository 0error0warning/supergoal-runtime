def test_replay_replan_changes_action_class_or_blocks(trace, replay):
    result = replay(trace("bitget_20260608_trace.jsonl"))
    state = result.managers["replay-bitget"].state

    assert state is not None
    assert state.replan_count > 0 or state.should_replan
    # The replay proposes infra repeatedly while SG-1 is open. Controller must
    # either rewrite the next action to the gate-aligned action or block it.
    assert state.action_proposal.action_class != "infra_engineering" or state.hard_gate_reason


def test_replay_action_targets_first_failed_blocking_gate_after_replan(trace, replay):
    result = replay(trace("bitget_20260608_trace.jsonl"))
    state = result.managers["replay-bitget"].state

    assert state is not None
    first_open = next((g for g in state.gates if g.status != "passed" and g.blocking), None)
    assert first_open is not None
    assert state.action_proposal.target_gate_id == first_open.id or state.hard_gate_reason
