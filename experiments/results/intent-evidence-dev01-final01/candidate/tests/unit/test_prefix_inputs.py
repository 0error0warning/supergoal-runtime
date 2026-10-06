import json

import pytest

from experiments.public_benchmarks.prefix_inputs import digest, first_registered_origins, load_prefix


def test_origin_selection_uses_registration_order_not_best_observed_score():
    cells = [{'task_id': 'p', 'arm': 'first'}, {'task_id': 'p', 'arm': 'second'}]
    rows = [{**c, 'status': 'returned', 'score': s} for c, s in zip(cells, [0, 1])]
    assert first_registered_origins({'planned_order': cells}, {'rows': rows})['p']['score'] == 0
    rows[0]['status'] = 'requires_audit'
    with pytest.raises(ValueError, match='do not select a substitute'):
        first_registered_origins({'planned_order': cells}, {'rows': rows})


@pytest.mark.parametrize('changed', ['public_requirement', 'grader_history'])
def test_unchanged_spec_cannot_hide_changed_prefix_inputs(tmp_path, changed):
    spec = {'schema': 1, 'prefix_checkpoint_count': 1, 'image_id': 'sha256:' + 'a' * 64, 'workspace_sha256': 'b' * 64}
    for name in ('public_requirement', 'grader_history'):
        p = tmp_path / name
        p.write_text('public requirement' if name == 'public_requirement' else '{"exists":false}')
        spec[name] = {'path': str(p), 'sha256': digest(p)}
    path = tmp_path / 'prefix.json'
    path.write_text(json.dumps(spec))
    expected = digest(path)
    assert load_prefix(path, expected)[1] == 'public requirement'
    (tmp_path / changed).write_text('changed')
    with pytest.raises(ValueError, match='Pinned prefix input changed'):
        load_prefix(path, expected)
