"""Failure/budget boundaries; the actual upstream role loop has a remote smoke gate."""
import asyncio
from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import sys
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def backend(monkeypatch):
    # Harbor and LongHorizon are pinned remote experiment dependencies, not
    # production plugin requirements. Their real ABI is tested by smoke01.
    for name, attrs in {
        'checkpoint_agent': {'SnapshotMixin': type('SnapshotMixin', (), {})},
        'harbor_agent': {'HermesSupergoalStudy': type('Study', (), {}),
                         'ROOT': Path('/unused'), 'dump': None},
        'model_proxy': {'ModelProxy': None},
        'lh_harness': {},
        'lh_harness.types': {'EpisodeResult': SimpleNamespace},
    }.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    path = Path(__file__).parents[2] / 'experiments/public_benchmarks/longhorizon_agent.py'
    spec = importlib.util.spec_from_file_location('longhorizon_backend_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_agent(result):
    return SimpleNamespace(max_requests=2, deadline=time.monotonic() + 120,
                           rounds=[], container='owned-container',
                           episode=AsyncMock(return_value=result))


def test_incomplete_sdk_cannot_promote_a_completion_claim(backend):
    agent = fixture_agent({'completed': False, 'final_response': 'Status: complete',
                           'error': 'stream disconnected'})
    result = asyncio.run(backend.HermesRole(agent, SimpleNamespace(records=[]), 'cli_auditor')
                         .run_episode('audit', None, SimpleNamespace(max_duration_seconds=30)))
    assert result.status == 'error'
    assert result.metadata['agent_done'] is False
    assert result.metadata['assistant_visible_output'] == 'Status: complete'
    assert result.error == 'stream disconnected'


@pytest.mark.parametrize('boundary', ['requests', 'deadline', 'gui'])
def test_unavailable_role_or_exhausted_budget_never_calls_model(backend, boundary):
    agent = fixture_agent({'completed': True})
    proxy = SimpleNamespace(records=[{}, {}] if boundary == 'requests' else [])
    if boundary == 'deadline':
        agent.deadline = time.monotonic() + 1
    role = 'gui_executor' if boundary == 'gui' else 'cli_executor'
    result = asyncio.run(backend.HermesRole(agent, proxy, role)
                         .run_episode('execute', None, SimpleNamespace(max_duration_seconds=30)))
    assert result.status == 'error'
    agent.episode.assert_not_awaited()


def test_cancellation_propagates_and_restores_shared_deadline(backend):
    agent = fixture_agent(None)
    original_deadline = agent.deadline

    async def cancelled(*args, **kwargs):
        assert agent.deadline < original_deadline
        assert kwargs['history'] is None
        agent.rounds.append({'role': kwargs['role']})
        raise asyncio.CancelledError

    agent.episode.side_effect = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(backend.HermesRole(agent, SimpleNamespace(records=[]), 'cli_auditor')
                    .run_episode('audit', None, SimpleNamespace(max_duration_seconds=30)))
    assert agent.deadline == original_deadline
    assert agent.rounds[0]['longhorizon_role'] == 'cli_auditor'
    assert agent.rounds[0]['role'] == 'review'


def test_host_transfers_are_confined_to_owned_control(backend, tmp_path):
    agent = SimpleNamespace(control=tmp_path / 'owned', uid='fixture')
    agent.control.mkdir()
    env = backend.ContainerEnvironment(agent, None)
    assert env.local_path(agent.control / 'trace.json').parent == agent.control
    with pytest.raises(ValueError, match='owned control'):
        env.local_path(agent.control / '..' / 'another-run' / 'trace.json')


def test_failed_snapshot_handoff_is_not_reported_as_harness_success(backend, tmp_path, monkeypatch):
    monkeypatch.setattr(backend, 'verify_upstream', lambda: None)
    monkeypatch.setattr(backend, 'ROOT', tmp_path)
    (tmp_path / 'private').mkdir()
    (tmp_path / 'private/model.json').write_text('{}')
    monkeypatch.setattr(backend, 'ModelProxy', lambda *args, **kwargs: nullcontext(
        SimpleNamespace(records=[], rejections=[])))
    monkeypatch.setattr(backend, 'dump', lambda path, data: path.write_text(json.dumps(data)))
    manager = ModuleType('lh_harness.manager')
    manager.run = AsyncMock(return_value={'completion_satisfied': True, 'status': 'complete'})
    monkeypatch.setitem(sys.modules, 'lh_harness.manager', manager)
    types = sys.modules['lh_harness.types']
    types.HarnessConfig = SimpleNamespace
    types.EpisodeBudget = lambda seconds: SimpleNamespace(max_duration_seconds=seconds)
    agent = object.__new__(backend.LongHorizonStudy)
    agent.uid, agent.cwd, agent.container = 'fixture', '/app', 'owned-container'
    agent.control = tmp_path / 'control'
    agent.control.mkdir()
    agent.budget_seconds, agent.max_requests, agent.max_episodes = 60, 2, 1
    agent.snapshot_receipts, agent.rounds = [], []

    async def failed_handoff(*args):
        agent.snapshot_receipts.append({'state_restored': False})
        raise RuntimeError('Container could not be unpaused')

    agent.docker = failed_handoff
    context = SimpleNamespace()
    asyncio.run(agent.run('fixture task', None, context))
    report = json.loads((agent.control / 'report.json').read_text())
    assert report['upstream_result']['completion_satisfied'] is True
    assert report['status'] == context.metadata['study_status'] == 'adapter_error'
    assert report['error_type'] == 'SnapshotStateError'
