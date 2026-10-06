import importlib.util
from pathlib import Path

_path = Path(__file__).parents[2] / 'experiments/tasks.py'
_spec = importlib.util.spec_from_file_location('research_tasks', _path)
tasks = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tasks)


def test_reference_is_sensitive_to_revision_timezone_delete_and_tie_order():
    def row(revision, value, instant='2026-06-01T12:00:00Z', op='upsert'):
        return {'id': 'a', 'revision': revision, 'value': value, 'observed_at': instant, 'op': op}
    cutoff = '2026-06-01T12:00:00Z'
    data = [row('2', 8), row('10', 0, '2026-06-01T20:00:00+08:00'), row('30', 999, '2026-06-01T12:00:01Z')]
    assert tasks.reference(data, cutoff) == [{'id': 'a', 'value': 0}]
    assert tasks.reference(data+[row('10', -3)], cutoff) == [{'id': 'a', 'value': -3}]
    assert tasks.reference(data+[row('11', 0, op='delete')], cutoff) == []


def test_development_fixtures_are_reproducible_and_keep_oracle_separate():
    for family in tasks.FAMILIES:
        task = tasks.build(family, 1101)
        assert task == tasks.build(family, 1101)
        assert task != tasks.build(family, 1102)
        assert 'private' in task and task['private']['expected']
        assert all(not x.startswith('../') for x in task['files'])
        public = {k: task[k] for k in ('id', 'family', 'seed', 'prompt', 'contract')}
        assert 'private' not in public
        assert 'expected' not in public['contract']
