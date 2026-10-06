"""Analysis guards: incomplete, duplicate and altered trials must not look final."""
import copy
import importlib.util
import json
from pathlib import Path

MODULE_PATH = Path(__file__).with_name('registered_results.py')
SPEC = importlib.util.spec_from_file_location('registered_results', MODULE_PATH)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def write(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def inputs(tmp_path, monkeypatch):
    # No executor is involved: reuse an already completed measurement as input.
    monkeypatch.setattr(analysis, 'ROOT', tmp_path)
    original = analysis.read(analysis.RESULTS/'holdout01a-results.json')[0]
    row = copy.deepcopy(original)
    task_id, arm = row['task_id'], row['arm']
    row['run_id'] = f'audit-fixture-{task_id}-{arm}'
    manifest = {'reserved_tasks': {task_id: row['task_sha256']}, 'model': 'devin/swe-2',
                'limits': {'outer_turns': 6, 'executor_calls': 48, 'all_physical_calls': 60}}
    write(tmp_path/'freeze.json', manifest)
    registration = {'manifest_file': 'freeze.json', 'manifest_file_sha256': analysis.sha(tmp_path/'freeze.json'),
                    'registered_utc': '2026-01-01T00:00:00+00:00', 'arms': [arm], 'runs': 1,
                    'batches': [{'tag': 'audit-fixture', 'seeds': [row['seed']]}]}
    write(tmp_path/'registration.json', registration)
    write(tmp_path/'rows.json', [row])
    return tmp_path/'registration.json', tmp_path/'rows.json', row


def test_incomplete_and_duplicate_attempts_cannot_be_final(tmp_path, monkeypatch):
    registration, path, row = inputs(tmp_path, monkeypatch)
    assert analysis.audit(registration, [path])['complete']
    write(path, [])
    result = analysis.audit(registration, [path])
    assert not result['complete'] and len(result['missing_runs']) == 1
    write(path, [row, row])
    result = analysis.audit(registration, [path])
    assert not result['complete']
    assert any('Duplicate run' in e for e in result['audit_errors'])


def test_changed_task_or_model_is_detected(tmp_path, monkeypatch):
    registration, path, row = inputs(tmp_path, monkeypatch)
    row['task_sha256'] = 'modified'
    row['requests'][0]['request_model'] = 'different-model'
    write(path, [row])
    result = analysis.audit(registration, [path])
    assert not result['complete']
    assert any('identity mismatch' in e for e in result['audit_errors'])
    assert any('Different requested model' in e for e in result['audit_errors'])


def test_missing_usage_is_reported_separately(tmp_path, monkeypatch):
    _, _, row = inputs(tmp_path, monkeypatch)
    row['requests'] = [{'usage': None}, {'usage': {'input_tokens': 5, 'output_tokens': 2, 'total_tokens': 7}}]
    totals = analysis.measured_tokens([row])
    assert totals['total_tokens'] == 7 and totals['usage_missing'] == 1
    assert totals['cached_input_tokens_missing'] == 1
