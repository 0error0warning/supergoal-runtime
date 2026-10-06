"""Scientific bookkeeping must distinguish task failures from broken grading."""

from experiments.public_benchmarks.collect_public import classify
from experiments.public_benchmarks.environment_bootstrap import parse_bootstrap
from experiments.public_benchmarks.analyze_public import analyze, paired_statistics

import pytest


def result(reward):
    return {"verifier_result": {"rewards": {"reward": reward}}, "exception_info": None}


def test_dependency_failure_zero_is_not_a_model_failure():
    stdout = "E: Failed to fetch http://deb.debian.org/package.deb 500\nuvx: command not found\n"
    assert classify(result(0), stdout) == "apparatus_dependency_failure"


def test_completed_failed_tests_keep_the_official_zero():
    stdout = "FAILED tests/test_outputs.py::test_output\n============= 1 failed, 2 passed in 1.23s =============\n"
    assert classify(result(0), stdout) == "graded"


def test_collection_error_requires_audit_even_when_reward_exists():
    stdout = "ERROR collecting /tests/test_outputs.py\nModuleNotFoundError\n========== 1 error in 0.1s ==========\n"
    assert classify(result(0), stdout) == "needs_verifier_audit"


def test_success_without_test_execution_is_not_validated_by_reward_alone():
    assert classify(result(1), "") == "needs_verifier_audit"


def test_harbor_empty_stdout_records_no_environment_change():
    assert parse_bootstrap(None) == []
    assert parse_bootstrap("") == []


def test_setup_failure_stays_visible_without_model_metadata():
    trial = {"job_name": "tb-public-dev04-01-task-native", "classification": "trial_exception",
             "result": {"task_name": "task", "config": {"agent": {"kwargs": {"arm": "native"}}}}}
    registration = {"experiment": "public-dev04", "arms": ["native"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}]}
    bundle = {"trials": [trial], "model_runs": {}, "collected_at": "test"}
    summary = analyze(bundle, registration)
    assert summary["observed_trials"] == 1
    assert summary["valid_trials"] == 0
    assert summary["rows"][0]["requests"] is None


def test_charged_requests_without_usage_do_not_become_zero_token_totals():
    row = {'job_name': 'tb-study-01-task-native', 'classification': 'graded',
           'result': {'task_name': 'task', 'agent_result': {'n_input_tokens': 0, 'n_output_tokens': 0,
               'metadata': {'study_arm': 'native', 'control_id': 'run'}},
               'verifier_result': {'rewards': {'reward': 0}}}}
    registration = {'experiment': 'study', 'arms': ['native'], 'tasks': [{'task_id': 'task'}], 'planned_order': [{}]}
    report = {'used_requests': 3, 'requests': [{'http_status': 503} for _ in range(3)]}
    bundle = {'trials': [row], 'model_runs': {'run': {'report': report}}, 'collected_at': 'test'}
    measured = analyze(bundle, registration)['rows'][0]
    assert measured['requests'] == 3
    assert measured['input_tokens'] is None and measured['output_tokens'] is None
    assert measured['observed_input_tokens'] == 0
    assert measured['requests_with_unmeasured_token_usage'] == 3
    assert measured['token_usage_complete'] is False


def test_duplicate_task_arm_is_an_error_instead_of_best_of_n():
    trial = {"job_name": "tb-public-dev04-01-task-native", "classification": "trial_exception",
             "result": {"task_name": "task", "config": {"agent": {"kwargs": {"arm": "native"}}}}}
    registration = {"experiment": "public-dev04", "arms": ["native"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}]}
    bundle = {"trials": [trial, trial], "model_runs": {}, "collected_at": "test"}
    with pytest.raises(ValueError, match="Duplicate"):
        analyze(bundle, registration)


def test_namespaced_official_task_is_counted_in_the_registered_pair():
    trials = []
    for arm, reward in [("native", 0), ("sg_v2", 1)]:
        trials.append({"job_name": f"tb-public-dev04-01-task-{arm}", "classification": "graded",
                       "result": {"task_name": "terminal-bench/task",
                                  "config": {"agent": {"kwargs": {"arm": arm}}},
                                  "verifier_result": {"rewards": {"reward": reward}}}})
    registration = {"experiment": "public-dev04", "arms": ["native", "sg_v2"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}, {}]}
    summary = analyze({"trials": trials, "model_runs": {}, "collected_at": "test"}, registration)
    assert summary["paired_native_full"] == {"native_0_sg_1": 1}
    assert [row["task_id"] for row in summary["rows"]] == ["task", "task"]


def test_controller_failure_is_not_dropped_from_end_to_end_pair():
    trials = []
    for arm in ["native", "sg_v2"]:
        trials.append({"job_name": f"tb-public-gcp01-01-task-{arm}",
                       "classification": "graded" if arm == "native" else "trial_exception",
                       "result": {"task_name": "task", "agent_result": {"metadata": {
                           "study_arm": arm, "control_id": arm}},
                           "verifier_result": {"rewards": {"reward": 1}} if arm == "native" else None}})
    registration = {"experiment": "public-gcp01", "arms": ["native", "sg_v2"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}, {}]}
    bundle = {"trials": trials, "collected_at": "test", "host": "supergoal-gcp",
              "model_runs": {"sg_v2": {"report": {"status": "adapter_error", "used_requests": 93}}}}
    summary = analyze(bundle, registration)
    assert summary["paired_native_full"] == {}
    assert summary["paired_native_full_end_to_end"] == {"native_1_sg_0": 1}
    assert summary["rows"][1]["official_raw_reward"] is None
    assert summary["end_to_end_by_arm"]["sg_v2"] == {
        "observed_evaluable_trials": 1, "successes": 0, "controller_failures": 1,
        "execution_deadlines": 0}


def test_paired_test_uses_task_discordances_not_total_model_calls():
    stats = paired_statistics({'native_0_sg_1': 6, 'native_1_sg_1': 18})
    assert stats['paired_tasks'] == 24
    assert stats['success_rate_difference'] == 0.25
    assert stats['mcnemar_exact_two_sided_p'] == 0.03125
    assert paired_statistics({}) == {'paired_tasks': 0}
    assert paired_statistics({'native_0_sg_1': 4, 'native_1_sg_0': 4})['mcnemar_exact_two_sided_p'] == 1.0


def test_snapshot_reference_does_not_prove_current_availability():
    trial = {"job_name": "tb-study-01-task-native", "classification": "graded",
             "result": {"task_name": "task", "agent_result": {"metadata": {
                 "study_arm": "native", "control_id": "run"}},
                 "verifier_result": {"rewards": {"reward": 1}}}}
    registration = {"experiment": "study", "arms": ["native"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}]}
    bundle = {"trials": [trial], "collected_at": "test", "model_runs": {
        "run": {"report": {"artifact_image_id": "sha256:missing"}}}}
    row = analyze(bundle, registration)["rows"][0]
    assert row["artifact_snapshot_recorded"] is True
    assert row["artifact_snapshot_available"] is None
    bundle["checkpoint_observations"] = {"sha256:missing": {
        "image_available": False, "retention_status": "capture_failed"}}
    row = analyze(bundle, registration)["rows"][0]
    assert row["artifact_snapshot_available"] is False
    assert row["artifact_retention_receipt"] == "capture_failed"


def test_audited_handoff_failure_counts_without_inventing_an_official_score():
    trial = {"job_name": "tb-study-01-task-native", "classification": "trial_exception",
             "result": {"task_name": "task", "agent_result": {"metadata": {
                 "study_arm": "native", "control_id": "run"}}, "verifier_result": None}}
    registration = {"experiment": "study", "arms": ["native"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}]}
    bundle = {"trials": [trial], "collected_at": "test", "model_runs": {
        "run": {"report": {"status": "agent_stopped"}}}}
    # Missing reward alone is not enough to attribute the cause to the harness.
    assert analyze(bundle, registration)["rows"][0]["end_to_end_reward"] is None
    bundle["adjudications"] = {"run": {"kind": "harness_snapshot_handoff_failure",
                                      "evidence": "matching daemon pause errors during verifier handoff"}}
    summary = analyze(bundle, registration)
    assert summary["rows"][0]["official_raw_reward"] is None
    assert summary["rows"][0]["end_to_end_reward"] == 0
    assert summary["end_to_end_by_arm"]["native"]["controller_failures"] == 1


def test_solver_deadline_counts_as_failure_without_claiming_controller_defect():
    trial = {"job_name": "tb-study-01-task-native", "classification": "trial_exception",
             "result": {"task_name": "task", "agent_result": {"metadata": {
                 "study_arm": "native", "control_id": "run"}},
                 "verifier_result": {"rewards": {"reward": 0}},
                 "exception_info": {"exception_type": "AgentTimeoutError"}}}
    registration = {"experiment": "study", "arms": ["native"],
                    "tasks": [{"task_id": "task"}], "planned_order": [{}]}
    bundle = {"trials": [trial], "collected_at": "test", "model_runs": {
        "run": {"report": {"status": "adapter_error"}}}}
    summary = analyze(bundle, registration)
    row = summary["rows"][0]
    assert row["end_to_end_reward"] == row["official_raw_reward"] == 0
    assert row["execution_limit_reached"] and not row["harness_failure"]
    assert summary["end_to_end_by_arm"]["native"]["execution_deadlines"] == 1
