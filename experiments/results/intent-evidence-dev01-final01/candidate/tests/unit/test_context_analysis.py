import copy

import pytest

from experiments.public_benchmarks.analyze_context import analyze
from experiments.public_benchmarks.context_policy import CONDITIONS


def inputs():
    cells = [{'task_id': 'p', 'arm': arm} for arm in CONDITIONS]
    reg = {'experiment': 'test', 'planned_order': cells, 'tasks': [{'task_id': 'p'}],
           'contrasts': [['carry_ledger', 'fresh_ledger'], ['carry_current', 'fresh_current']]}
    bundle = {'collected_at': 'fixed', 'cohort_status': 'complete', 'problem_ledgers': {}, 'child_controls': {},
              'rows': [{**cell, 'status': 'returned', 'scored_checkpoints': 2, 'expected_checkpoints': 2,
                        'mean_available_checkpoint_strict': score, 'charged_physical_requests': 10}
                       for cell, score in zip(cells, [.8, .4, .6, .3])]}
    return bundle, reg


def test_problem_factorial_difference_is_not_checkpoint_sample_count():
    bundle, reg = inputs()
    result = analyze(bundle, reg)
    assert result['statistical_units'] == 1
    assert result['interactions'][0]['difference_in_differences'] == pytest.approx(.1)
    assert result['contrasts'][0]['pairs'][0]['strict_mean_difference'] == pytest.approx(-.2)


def test_partial_phase_mean_is_not_used_as_complete_problem_endpoint():
    bundle, reg = inputs()
    bundle['rows'][0]['scored_checkpoints'] = 1
    result = analyze(bundle, reg)
    assert result['rows'][0]['available_checkpoint_mean'] == .8
    assert result['rows'][0]['complete_strict_mean'] is None
    assert result['interactions'][0]['difference_in_differences'] is None
    assert result['contrasts'][0]['pairs'][0]['strict_mean_difference'] is None
    assert result['contrasts'][0]['pairs'][0]['request_difference'] == 0


def test_duplicate_condition_is_rejected():
    bundle, reg = inputs()
    bundle['rows'].append(copy.deepcopy(bundle['rows'][0]))
    with pytest.raises(ValueError):
        analyze(bundle, reg)


def test_actual_payload_disagreement_is_not_counted_as_activated():
    bundle, reg = inputs()
    bundle['rows'][0]['problem_id'] = 'problem'
    declared = {'condition': 'carry_ledger', 'prior_public_requirements': 0,
                'prior_requirements_replayed': 0, 'incoming_history_messages': 0,
                'incoming_history_sha256': None, 'public_prompt_sha256': 'goal'}
    first = {'history_messages': 0, 'history_sha256': None, 'prompt_sha256': 'prompt'}
    bundle['problem_ledgers']['problem'] = {'steps': [{'control_id': 'child', 'context_input': declared}]}
    bundle['child_controls']['child'] = {'first_executor_payload': {'present': True, 'history_sha256': 'unexpected',
        'history_messages': 1, 'goal_sha256': 'goal', 'prompt_sha256': 'prompt'}, 'report': {'executor_contexts': [first]}}
    result = analyze(bundle, reg)
    assert result['audit_errors'][0]['kind'] == 'actual_context_mismatch'
    assert result['rows'][0]['activations'][0]['actual_payload_matches_declared_policy'] is False


def test_matching_input_declarations_cannot_hide_wrong_prior_history():
    bundle, reg = inputs()
    bundle['rows'][0]['problem_id'] = 'problem'
    declared = {'condition': 'carry_ledger', 'prior_public_requirements': 1,
                'prior_requirements_replayed': 1, 'incoming_history_messages': 2,
                'incoming_history_sha256': 'wrong-output', 'public_prompt_sha256': 'goal'}
    first = {'history_messages': 2, 'history_sha256': 'wrong-output', 'prompt_sha256': 'prompt'}
    bundle['problem_ledgers']['problem'] = {'steps': [
        {'outgoing_history_messages': 2, 'outgoing_history_sha256': 'real-output'},
        {'control_id': 'child', 'context_input': declared}]}
    bundle['child_controls']['child'] = {'first_executor_payload': {'present': True,
        'history_sha256': 'wrong-output', 'history_messages': 2, 'goal_sha256': 'goal', 'prompt_sha256': 'prompt'},
        'report': {'executor_contexts': [first]}}
    result = analyze(bundle, reg)
    assert any(r['kind'] == 'carried_history_differs_from_previous_output' for r in result['audit_errors'])
    assert not result['rows'][0]['activations'][1]['history_carry_exercised']


def test_paired_suffix_replay_activates_at_first_new_phase():
    bundle, reg = inputs()
    arms = ['fresh_ledger', 'fresh_current']
    reg.update(arms=arms, prefix_checkpoint_count=1, contrasts=[['fresh_current', 'fresh_ledger']])
    reg['planned_order'] = [c for c in reg['planned_order'] if c['arm'] in arms]
    bundle['rows'] = [r for r in bundle['rows'] if r['arm'] in arms]
    row = bundle['rows'][0]
    row['problem_id'] = 'problem'
    declared = {'condition': 'fresh_ledger', 'prior_public_requirements': 1,
                'prior_requirements_replayed': 1, 'incoming_history_messages': 0,
                'incoming_history_sha256': None, 'public_prompt_sha256': 'goal'}
    first = {'history_messages': 0, 'history_sha256': None, 'prompt_sha256': 'prompt'}
    bundle['problem_ledgers']['problem'] = {'steps': [{'control_id': 'child', 'context_input': declared}]}
    bundle['child_controls']['child'] = {'first_executor_payload': {'present': True, 'history_sha256': None,
        'history_messages': 0, 'goal_sha256': 'goal', 'prompt_sha256': 'prompt'},
        'report': {'executor_contexts': [first]}}
    result = analyze(bundle, reg)
    assert result['interactions'] == []
    assert result['audit_errors'] == []
    assert result['rows'][0]['activations'][0]['checkpoint'] == 2
    assert result['rows'][0]['activations'][0]['requirement_replay_exercised']
    assert result['contrasts'][0]['pairs'][0]['strict_mean_difference'] == pytest.approx(.3)
    declared['prior_public_requirements'] = 0
    result = analyze(bundle, reg)
    assert result['audit_errors'][0]['kind'] == 'actual_context_mismatch'
    assert not result['rows'][0]['activations'][0]['requirement_replay_exercised']
