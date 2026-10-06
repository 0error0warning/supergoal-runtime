from experiments.public_benchmarks.collect_multistep import summarize_problem, checkpoint_diagnostic
from experiments.infrastructure.prepare_multistep_study import adapt_config
import tomllib


def test_perfect_available_checkpoint_is_not_a_completed_problem():
    raw = {'step_results': [{'verifier_result': {'rewards': {'strict_pass_rate': 1.0}}}]}
    result = summarize_problem(raw, 3)
    assert result['mean_available_checkpoint_strict'] == 1
    assert not result['all_registered_checkpoints_strictly_passed']


def test_unscored_checkpoint_cannot_disappear_from_completion_requirement():
    raw = {'step_results': [{'verifier_result': {'rewards': {'strict_pass_rate': 1.0}}},
                           {'exception_info': {'exception_type': 'AgentTimeoutError'}}]}
    result = summarize_problem(raw, 2)
    assert result['scored_checkpoints'] == 1
    assert not result['all_registered_checkpoints_strictly_passed']


def test_passing_artifact_after_solver_timeout_keeps_protocol_failure():
    raw = {'step_results': [{'verifier_result': {'rewards': {'strict_pass_rate': 1.0}},
                            'exception_info': {'exception_type': 'AgentTimeoutError'}}]}
    result = summarize_problem(raw, 1)
    assert result['all_registered_checkpoints_strictly_passed']
    assert not result['complete_without_execution_or_grading_exception']


def test_model_pass_and_partial_strict_score_remain_distinct():
    step = {'step_name': 'checkpoint_1', 'verifier_result': {'rewards': {
        'core_pass_rate': 1.0, 'strict_pass_rate': 0.625}}}
    report = {'last_acceptance': {'verdict': 'pass'}, 'status': 'succeeded'}
    result = checkpoint_diagnostic(step, report, {'core_collected': 1})
    assert result['online_pass_with_strict_shortfall']
    assert result['official_reward_details'] == {'core_collected': 1}


def test_failed_strict_tests_are_not_execution_or_grading_exceptions():
    raw = {'step_results': [{'verifier_result': {'rewards': {'strict_pass_rate': 0.5}}}]}
    result = summarize_problem(raw, 1)
    assert result['all_checkpoints_graded_without_exception']
    assert not result['all_registered_checkpoints_strictly_passed']
    assert not result['complete_without_execution_or_grading_exception']


def test_incomplete_sdk_is_visible_without_a_harbor_exception():
    step = {'step_name': 'checkpoint_1', 'verifier_result': {'rewards': {'strict_pass_rate': 0}}}
    report = {'status': 'worker_incomplete', 'requests': [{'http_status': 503, 'error': 'HTTPError'}]}
    result = checkpoint_diagnostic(step, report, {})
    assert result['worker_incomplete']
    assert result['upstream_http_errors'] == {'503': 1}
    assert result['transport_error_calls'] == 1


def test_task_adaptation_preserves_grader_deadlines_and_artifact_contract():
    original = '''schema_version = "1.1"
[environment]
cpus = 1
[agent]
timeout_sec = 7200.0
[verifier]
timeout_sec = 1800.0
[[steps]]
name = "checkpoint_1"
artifacts = [{source = "/app", destination = "app"}]
[steps.agent]
timeout_sec = 7200.0
[steps.verifier]
timeout_sec = 1800.0
'''
    result = tomllib.loads(adapt_config(original, 'sha256:fixed', 1))
    assert result['agent']['timeout_sec'] == result['steps'][0]['agent']['timeout_sec'] == 1800
    assert result['verifier']['timeout_sec'] == result['steps'][0]['verifier']['timeout_sec'] == 1800
    assert result['steps'][0]['artifacts'] == [{'source': '/app', 'destination': 'app'}]
