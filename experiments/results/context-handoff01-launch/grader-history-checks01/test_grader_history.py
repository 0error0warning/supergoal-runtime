import asyncio
import os
from pathlib import Path
import shlex
import sys
from types import SimpleNamespace

import pytest

from experiments.public_benchmarks import grader_history


pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != 'posix', reason='POSIX owner/mode boundary')]


class LocalEnvironment:
    async def exec(self, command, *, user, timeout_sec):
        args = shlex.split(command)
        assert args[:2] == ['python3', '-c']
        child = await asyncio.create_subprocess_exec(sys.executable, *args[1:],
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(child.communicate(), timeout_sec)
        return SimpleNamespace(return_code=child.returncode, stdout=stdout.decode(), stderr=stderr.decode())


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    history = tmp_path / 'model-visible' / 'history.jsonl'
    history.parent.mkdir()
    monkeypatch.setattr(grader_history, 'REMOTE', str(history))
    return history, tmp_path / 'host-backup.json', LocalEnvironment()


async def test_preserves_original_bytes_owner_mode_and_repeated_restore(boundary):
    path, backup, environment = boundary
    original = b'\x00\xff\n' + '中文'.encode()
    path.write_bytes(original)
    path.chmod(0o640)
    before = path.stat()
    row = await grader_history.conceal(environment, backup)
    assert backup.exists() and row['original_exists'] and not path.exists()
    await grader_history.restore(environment, backup)
    await grader_history.restore(environment, backup)
    after = path.stat()
    assert path.read_bytes() == original
    assert (after.st_uid, after.st_gid, after.st_mode) == (before.st_uid, before.st_gid, before.st_mode)


async def test_absence_stays_absent(boundary):
    path, backup, environment = boundary
    assert not (await grader_history.conceal(environment, backup))['original_exists']
    await grader_history.restore(environment, backup)
    assert not path.exists()


async def test_symlink_is_refused_without_touching_target(boundary):
    path, backup, environment = boundary
    target = path.parent / 'unrelated'
    target.write_text('keep')
    path.symlink_to(target)
    with pytest.raises(RuntimeError):
        await grader_history.conceal(environment, backup)
    assert target.read_text() == 'keep' and path.is_symlink() and not backup.exists()


async def test_model_replacement_cannot_be_silently_accepted(boundary):
    path, backup, environment = boundary
    path.write_bytes(b'original')
    await grader_history.conceal(environment, backup)
    saved = backup.read_bytes()
    path.write_bytes(b'altered')
    with pytest.raises(RuntimeError):
        await grader_history.restore(environment, backup)
    assert backup.read_bytes() == saved and path.read_bytes() == b'altered'


async def test_backup_corruption_is_detected(boundary):
    path, backup, environment = boundary
    path.write_bytes(b'original')
    await grader_history.conceal(environment, backup)
    content = backup.read_text().replace('b3JpZ2luYWw=', 'd3Jvbmc=')
    Path(backup).write_text(content)
    with pytest.raises(RuntimeError):
        await grader_history.restore(environment, backup)
    assert not path.exists()
