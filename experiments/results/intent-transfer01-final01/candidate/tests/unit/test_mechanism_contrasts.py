from experiments.public_benchmarks.analyze_public import mechanism_contrasts, paired_arm_comparisons


def test_activation_does_not_drop_short_tasks_from_assigned_comparison():
    rows = []
    for task, episodes, scores in [('short', 1, (1, 1)), ('long', 3, (0, 1))]:
        for arm, score in zip(['a', 'b'], scores):
            rows.append({'task_id': task, 'arm': arm, 'end_to_end_reward': score,
                         'requests': 10 if arm == 'a' else 20, 'executor_episodes': episodes})
    result = mechanism_contrasts(rows, [{'control': 'a', 'treatment': 'b'}])[0]
    assert result['paired_tasks'] == 2
    assert result['mean_reward_difference'] == 0.5
    assert sum(p['second_episode_observed'] for p in result['pairs']) == 1


def test_apparatus_missing_score_is_not_filled_with_a_model_zero():
    rows = [{'task_id': 'task', 'arm': arm, 'end_to_end_reward': score,
             'requests': 3, 'executor_episodes': 1} for arm, score in [('a', None), ('b', 1)]]
    assert mechanism_contrasts(rows, [{'control': 'a', 'treatment': 'b'}])[0]['paired_tasks'] == 0


def test_control_continuation_does_not_activate_treatment_history_reset():
    rows = [{'task_id': 'task', 'arm': arm, 'end_to_end_reward': 1,
             'requests': 10, 'executor_episodes': episodes, 'history_reset_exercised': False}
            for arm, episodes in [('sg_no_context', 2), ('sg_fresh_no_context', 1)]]
    result = mechanism_contrasts(rows, [{'control': 'sg_no_context', 'treatment': 'sg_fresh_no_context'}])[0]
    pair = result['pairs'][0]
    assert pair['second_episode_observed'] and pair['control_second_episode_observed']
    assert not pair['treatment_second_episode_observed']
    assert not pair['treatment_history_reset_exercised']


def test_paired_usage_excludes_unpaired_outcomes_and_preserves_unknown_cost():
    rows = []
    for task, scores, requests in [
        ('win', (0, 1), (5, 10)),
        ('loss', (1, 0), (None, 9)),
        ('unresolved', (None, 1), (1000, 2000)),
    ]:
        for arm, score, used in zip(['mini_swe', 'sg_v2'], scores, requests):
            rows.append({'task_id': task, 'arm': arm, 'end_to_end_reward': score,
                         'requests': used, 'input_tokens': None, 'output_tokens': None})
    rows.append({'task_id': 'not_paired', 'arm': 'mini_swe', 'end_to_end_reward': 1,
                 'requests': 9000})
    comparison = paired_arm_comparisons(rows, ['mini_swe', 'sg_v2'])[0]
    assert comparison['task_ids'] == ['loss', 'win']
    assert comparison['paired_tasks'] == 2
    assert comparison['sg_only_success'] == comparison['other_only_success'] == 1
    assert comparison['mean_reward_difference_sg_minus_other'] == 0
    assert comparison['paired_usage']['requests'] == {
        'paired_tasks': 1, 'other_total': 5, 'sg_total': 10}
    assert comparison['paired_usage']['input_tokens'] == {
        'paired_tasks': 0, 'other_total': None, 'sg_total': None}


def test_no_complete_pair_is_unknown_instead_of_zero_effect():
    comparison = paired_arm_comparisons([], ['native', 'sg_v2'])[0]
    assert comparison['paired_tasks'] == 0
    assert comparison['mean_reward_difference_sg_minus_other'] is None
    assert paired_arm_comparisons([], ['mini_swe']) == []
