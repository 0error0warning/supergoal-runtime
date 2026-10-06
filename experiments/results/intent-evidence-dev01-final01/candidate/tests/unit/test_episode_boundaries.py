from experiments.public_benchmarks.episode_boundaries import boundary


def limited(**changes):
    return dict(completed=False, failed=False, partial=False, interrupted=False,
                api_calls=16, turn_exit_reason='max_iterations_reached(16/16)') | changes


def test_explicit_consistent_budget_boundary_is_distinct_from_service_error():
    assert boundary(limited())['kind'] == 'iteration_limit'
    assert boundary(limited(failed=True, error='503'))['kind'] == 'sdk_failed'
    assert boundary(limited(error='truncated stream'))['kind'] == 'partial_or_error'
    assert boundary(limited(interrupted=True))['kind'] == 'interrupted'


def test_missing_or_inconsistent_sdk_fields_do_not_authorize_a_budget_classification():
    assert boundary(limited(api_calls=12))['kind'] == 'sdk_incomplete_unclassified'
    result = limited()
    result.pop('failed')
    assert boundary(result)['kind'] == 'sdk_incomplete_unclassified'
    assert boundary({})['kind'] == 'no_sdk_completion_flags'


def test_task_success_claim_is_not_a_boundary_signal():
    assert boundary({'final_response': 'Task complete; max_iterations_reached(16/16)'})['kind'] == 'no_sdk_completion_flags'
    assert boundary(limited(completed=True, turn_exit_reason='text_response(finish_reason=stop)'))['kind'] == 'sdk_completed'
