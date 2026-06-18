def test_replay_compression_preserves_goal_run_id(trace, replay):
    result = replay(trace("compression_split_trace.jsonl"))
    old = result.managers["replay-compress-old"].state
    new = result.managers["replay-compress-new"].state

    assert old is not None
    assert new is not None
    assert old.goal_run_id == new.goal_run_id
    assert len(set(gid for gid in result.goal_run_ids if gid)) == 1


def test_replay_compression_continues_same_logical_mission(trace, replay):
    result = replay(trace("compression_split_trace.jsonl"))
    new = result.managers["replay-compress-new"].state
    decision = result.decisions[-1]

    assert new is not None
    assert new.status == "active"
    assert decision["should_continue"] is True
    assert decision["continuation_prompt"]
