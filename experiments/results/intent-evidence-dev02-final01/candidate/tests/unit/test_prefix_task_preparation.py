from pathlib import Path
import tomllib

import pytest


def helper(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'experiments/public_benchmarks'))
    from experiments.infrastructure.prepare_prefix_study import suffix_toml
    return suffix_toml


def test_suffix_preserves_remaining_verifier_limits_and_environment(monkeypatch):
    transform = helper(monkeypatch)
    text = '''[environment]
docker_image = "sha256:old"
memory_mb = 2048
[agent]
timeout_sec = 1800
[[steps]]
name = "checkpoint_1"
[steps.verifier]
timeout_sec = 600
[[steps]]
name = "checkpoint_2"
[steps.verifier]
timeout_sec = 1200
'''
    changed = tomllib.loads(transform(text, 'sha256:new'))
    original = tomllib.loads(text)
    assert changed['steps'] == original['steps'][1:]
    assert changed['agent'] == original['agent']
    assert changed['environment'] == {'docker_image': 'sha256:new', 'memory_mb': 2048}


def test_wrong_first_checkpoint_is_not_silently_removed(monkeypatch):
    transform = helper(monkeypatch)
    with pytest.raises(ValueError, match='Unexpected source step structure'):
        transform('''[environment]
docker_image = "sha256:old"
[[steps]]
name = "checkpoint_2"
[[steps]]
name = "checkpoint_3"
''', 'sha256:new')
