from experiments.public_benchmarks.audit_multistep_regressions import parse_outcomes, transitions


def test_regression_requires_same_test_id_and_complete_inventories():
    old = parse_outcomes('collected 2 items\nPASSED tests/test_a.py::test_old\nFAILED tests/test_a.py::test_fixed - mismatch')
    new = parse_outcomes('collected 3 items\nFAILED tests/test_a.py::test_old - mismatch\nPASSED tests/test_a.py::test_fixed\nFAILED tests/test_b.py::test_new - missing')
    result = transitions(old, new)
    assert result['pass_to_fail'] == ['tests/test_a.py::test_old']
    assert result['fail_to_pass'] == ['tests/test_a.py::test_fixed']
    assert result['common_test_ids'] == 2


def test_missing_or_duplicate_summary_cannot_imply_no_regressions():
    complete = parse_outcomes('collected 1 item\nPASSED tests/test_a.py::test_old')
    for text in ['collected 2 items\nPASSED tests/test_a.py::test_old',
                 'collected 1 item\nPASSED tests/test_a.py::test_old\nFAILED tests/test_a.py::test_old - ambiguous']:
        result = transitions(complete, parse_outcomes(text))
        assert result == {'status': 'unresolved_incomplete_log_inventory'}


def test_explicit_anonymous_skip_is_accounted_without_guessing_test_identity():
    old = parse_outcomes('collected 2 items\nPASSED tests/test_a.py::test_old\nPASSED tests/test_a.py::test_obsolete')
    new = parse_outcomes('collected 2 items\nFAILED tests/test_a.py::test_old - mismatch\nSKIPPED [1] tests/test_a.py:43: Applies only to checkpoints 1-2')
    result = transitions(old, new)
    assert new['all_collected_outcomes_accounted']
    assert not new['complete_test_id_inventory']
    assert result['pass_to_fail'] == ['tests/test_a.py::test_old']
    assert result['old_ids_not_repeated'] == ['tests/test_a.py::test_obsolete']
    assert result['identity_scope'].startswith('reported_test_ids_only')


def test_first_suffix_is_compared_with_pinned_prefix_log(tmp_path):
    import hashlib
    import json
    import pytest
    from experiments.public_benchmarks.audit_multistep_regressions import audit

    task = tmp_path / 'task'
    task.mkdir()
    prefix = tmp_path / 'prefix.txt'
    prefix.write_text('collected 1 item\nPASSED tests/test_checkpoint_1.py::test_old')
    registered_hash = hashlib.sha256(prefix.read_bytes()).hexdigest()
    reg = {'experiment': 'test', 'tasks': [{'task_id': 'p', 'path': str(task), 'files_sha256': {},
        'prefix_checkpoint_count': 1, 'prefix_test_log': str(prefix), 'prefix_test_log_sha256': registered_hash}],
        'planned_order': [{'task_id': 'p', 'arm': 'fresh_ledger'}]}
    trial = tmp_path / 'jobs/test-01-p-fresh_ledger/trial'
    log = trial / 'steps/checkpoint_2/verifier/test-stdout.txt'
    log.parent.mkdir(parents=True)
    log.write_text('collected 2 items\nFAILED tests/test_checkpoint_1.py::test_old - changed\nPASSED tests/test_checkpoint_2.py::test_new')
    (trial / 'result.json').write_text(json.dumps({'step_results': [{'step_name': 'checkpoint_2'}]}))
    result = audit(tmp_path, reg)['rows'][0]
    assert len(result['checkpoints']) == 1
    assert result['prefix_checkpoint']['reused_prefix']
    assert result['transitions'][0]['from_checkpoint'] == 'checkpoint_1'
    assert result['transitions'][0]['pass_to_fail'] == ['tests/test_checkpoint_1.py::test_old']
    prefix.write_text('collected 1 item\nFAILED tests/test_checkpoint_1.py::test_old')
    with pytest.raises(ValueError, match='prefix test log changed'):
        audit(tmp_path, reg)
