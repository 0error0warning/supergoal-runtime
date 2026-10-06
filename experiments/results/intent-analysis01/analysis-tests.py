from experiments.public_benchmarks.analyze_intent_study import summarize_row


def row(task='terminal', **updates):
    return {'index': 0, 'task_id': task, 'arm': 'evidence', 'status': 'returned',
            'started_at': '2026-10-06T12:00:00+00:00', **updates}


def test_research_delivery_and_model_pass_are_never_terminal_success():
    result = summarize_row(row('dr3-014', raw_rewards={'delivery_present': 1}),
                           {'last_acceptance': {'verdict': 'pass'}}, None)
    assert result['research_delivery_present'] == 1
    assert result['terminal_end_to_end_success'] is None
    assert result['raw_terminal_reward'] is None
    assert not result['official_terminal_grade_returned']


def test_missing_usage_is_unknown_and_authoritative_journal_wins():
    result = summarize_row(row(), {'used_requests': 1, 'requests': []}, {'records': [
        {'usage': {'input_tokens': 100, 'output_tokens': 5}}, {'usage': {'input_tokens': 20}, 'error': 'timeout'}]})
    assert result['requests'] == 2
    assert result['observed_input_tokens'] == 120
    assert result['input_tokens'] is None
    assert result['requests_missing_usage'] == 1
    assert not result['usage_complete']


def test_unknown_is_separate_from_false_rejection_and_trial_errors_count():
    passing = summarize_row(row(raw_rewards={'reward': 1}), {'last_acceptance': {'verdict': 'unknown'}}, None)
    assert passing['unknown_with_passing_artifact']
    assert not passing['false_online_rejection']
    failed = summarize_row(row(exception={'exception_type': 'AgentTimeoutError'}), {}, None)
    assert failed['terminal_end_to_end_success'] == 0
    assert failed['raw_terminal_reward'] is None
