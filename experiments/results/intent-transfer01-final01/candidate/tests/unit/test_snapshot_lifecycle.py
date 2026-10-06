"""Exercise failure boundaries and pause ownership without a local Docker."""
import asyncio
import json

import pytest

from experiments.public_benchmarks.snapshot_lifecycle import capture_snapshot, SnapshotStateError


class Docker:
    def __init__(self, *, paused=False, failure=None, restore_failure=False, labels=None):
        self.state = {'Running': True, 'Paused': paused}
        self.failure, self.restore_failure = failure, restore_failure
        self.calls = []
        self.labels = labels or {}

    async def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if args[0] == 'inspect':
            return json.dumps([{'State': self.state, 'Config': {'Labels': self.labels}}])
        if args[0] == 'pause':
            self.state['Paused'] = True
        elif args[0] == 'unpause':
            if self.restore_failure:
                raise RuntimeError('daemon unavailable')
            self.state['Paused'] = False
        elif args[0] == 'commit':
            assert args[1] == '--no-pause'
            if self.failure:
                raise self.failure
            return 'sha256:' + 'a' * 64
        return ''


@pytest.mark.parametrize('failure', [TimeoutError('commit timeout'), asyncio.CancelledError()])
def test_failed_or_cancelled_capture_restores_running_actor(failure):
    docker, receipt = Docker(failure=failure), {}
    with pytest.raises(type(failure)):
        asyncio.run(capture_snapshot(docker, 'actor', 'snapshot:tag', receipt))
    assert docker.state == {'Running': True, 'Paused': False}
    assert receipt['state_restored'] is True
    assert receipt['status'] == 'capture_failed'
    assert 'image_id' not in receipt


def test_nested_capture_preserves_supervisors_pause():
    docker, receipt = Docker(paused=True), {}
    assert asyncio.run(capture_snapshot(docker, 'actor', 'snapshot:tag', receipt)) == 'sha256:' + 'a' * 64
    assert docker.state['Paused'] is True
    assert not any(c[0] in ('pause', 'unpause') for c in docker.calls)


def test_failed_state_restoration_cannot_look_like_safe_handoff():
    docker, receipt = Docker(restore_failure=True), {}
    with pytest.raises(SnapshotStateError):
        asyncio.run(capture_snapshot(docker, 'actor', 'snapshot:tag', receipt))
    assert receipt['state_restored'] is False


def test_checkpoint_is_detached_from_compose_cleanup_ownership():
    docker, receipt = Docker(labels={'com.docker.compose.project': 'trial', 'org.example.owner': 'user'}), {}
    asyncio.run(capture_snapshot(docker, 'actor', 'snapshot:tag', receipt))
    commit = next(c for c in docker.calls if c[0] == 'commit')
    assert 'LABEL com.docker.compose.project=""' in commit
    assert not any('org.example.owner' in c for c in commit)
    assert receipt['detached_compose_labels'] == ['com.docker.compose.project']
