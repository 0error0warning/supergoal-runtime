import pytest

from experiments.public_benchmarks.analyze_context_details import elapsed, module_groups, usage


def test_elapsed_normalizes_offsets_but_does_not_invent_missing_finish():
    assert elapsed({'started_at': '2026-10-06T08:00:00+08:00', 'finished_at': '2026-10-06T00:01:30Z'}) == 90
    assert elapsed({'started_at': '2026-10-06T00:00:00Z'}) is None
    with pytest.raises(ValueError):
        elapsed({'started_at': '2026-10-06T00:00:02Z', 'finished_at': '2026-10-06T00:00:01Z'})


def test_anonymous_skip_and_unmapped_tests_are_not_assigned_to_new_requirements():
    result = module_groups({'checkpoint': 'checkpoint_2', 'all_collected_outcomes_accounted': True,
                            'anonymous_skipped_summaries': [{'count': 1, 'reason': 'only in stage one'}],
                            'outcomes': {'../tests/test_checkpoint_1.py::a': 'PASSED',
                                         '../tests/test_checkpoint_2.py::b': 'FAILED',
                                         '../tests/test_misc.py::c': 'PASSED'}})
    assert result['groups']['prior_modules'] == {'PASSED': 1}
    assert result['groups']['current_module'] == {'FAILED': 1}
    assert result['groups']['unmapped_modules'] == {'PASSED': 1}
    assert result['anonymous_skipped_summaries'][0]['count'] == 1


def test_partial_token_record_and_missing_reserved_call_make_totals_unknown():
    row = {'charged_physical_requests': 2, 'observed_input_tokens': 14, 'observed_output_tokens': 0}
    controls = {'c': {'charged_requests': 2, 'report': {'requests': [{'usage': {'input_tokens': 14}}]}}}
    result = usage(row, controls, {'children': ['c']})
    assert result['requests_with_incomplete_token_usage'] == 2
    assert result['input_tokens'] is None and result['output_tokens'] is None
    assert result['observed_input_tokens'] == 14
