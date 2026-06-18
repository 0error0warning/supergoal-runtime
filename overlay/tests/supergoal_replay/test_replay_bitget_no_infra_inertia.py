def test_replay_bitget_infra_cannot_bypass_open_strategy_gate(trace, replay):
    result = replay(trace("bitget_20260608_trace.jsonl"))
    mgr = result.managers["replay-bitget"]
    state = mgr.state

    assert state is not None
    assert state.hard_gate_reason
    assert "infra_engineering" in state.hard_gate_reason
    assert state.action_proposal.action_class == "hypothesis_generation"
    assert state.action_proposal.target_gate_id == "SG-1"
    assert any(d["continuation_prompt"] and "HARD GATE / INERTIA GUARD" in d["continuation_prompt"] for d in result.decisions)


def test_replay_same_blocking_gate_no_evidence_blocks_or_no_edge(trace, replay):
    result = replay(trace("bitget_20260608_trace.jsonl"))
    state = result.managers["replay-bitget"].state

    assert state is not None
    assert state.same_action_no_evidence_count >= 1 or state.hard_gate_reason
    assert state.hard_gate_reason or state.no_edge_report
