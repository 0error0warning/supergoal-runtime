"""Durable Docker-save checkpoints with shared content-addressed layer blobs.

Tags in the experiment daemon are expendable references. A complete manifest is
published only after every archive member is hashed and fsynced. No image is run
during storage or restore. This adapter stores filesystem/image state, not live
process state or Docker volumes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import tempfile
import time


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class DockerCheckpointStore:
    def __init__(self, root, docker):
        self.root, self.docker = Path(root), list(docker)
        self.blobs = self.root / 'blobs'
        self.manifests = self.root / 'manifests'
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.manifests.mkdir(parents=True, exist_ok=True)

    def _blob(self, stream):
        digest = hashlib.sha256()
        size = 0
        fd, temp = tempfile.mkstemp(prefix='.pending-', dir=self.blobs)
        temp = Path(temp)
        try:
            with os.fdopen(fd, 'wb') as out:
                while chunk := stream.read(1024 * 1024):
                    out.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            key = digest.hexdigest()
            destination = self.blobs / key
            if destination.exists():
                # Recheck existing bytes before making a new manifest depend on
                # them; a size match alone is not an integrity check.
                with destination.open('rb') as existing:
                    if hashlib.file_digest(existing, 'sha256').hexdigest() != key:
                        raise ValueError('Stored checkpoint blob is corrupt: ' + key)
                temp.unlink()
            else:
                temp.replace(destination)
                sync_directory(self.blobs)
            return key, size
        finally:
            temp.unlink(missing_ok=True)

    def save(self, image):
        inspection = json.loads(subprocess.check_output(self.docker + ['image', 'inspect', image], text=True))[0]
        image_id = inspection['Id']
        target = self.manifests / (image_id.removeprefix('sha256:') + '.json')
        if target.exists():
            self.verify(target)
            return target
        # Docker's stderr never becomes artifact content or a model prompt.
        with tempfile.TemporaryFile() as errors:
            proc = subprocess.Popen(self.docker + ['image', 'save', image], stdout=subprocess.PIPE, stderr=errors)
            members = []
            try:
                with tarfile.open(fileobj=proc.stdout, mode='r|') as archive:
                    for item in archive:
                        path = PurePosixPath(item.name)
                        if path.is_absolute() or '..' in path.parts:
                            raise ValueError('Unsafe Docker archive path')
                        if item.isdir():
                            continue
                        if not item.isfile():
                            raise ValueError('Unexpected Docker archive member type')
                        stream = archive.extractfile(item)
                        key, size = self._blob(stream)
                        members.append({'path': item.name, 'sha256': key, 'size': size, 'mode': item.mode})
                if proc.wait(timeout=60):
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode(errors='replace')[-1500:])
            finally:
                proc.stdout.close()
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        manifest = {'schema': 1, 'kind': 'docker_save_cas', 'image_id': image_id,
                    'original_tags': inspection.get('RepoTags', []), 'created_at_epoch': time.time(),
                    'members': members, 'process_state_included': False, 'mounted_volumes_included': False}
        with tempfile.NamedTemporaryFile(mode='w', dir=self.manifests, delete=False, encoding='utf-8') as out:
            json.dump(manifest, out, indent=2)
            out.flush()
            os.fsync(out.fileno())
            pending = Path(out.name)
        pending.replace(target)
        sync_directory(self.manifests)
        return target

    def verify(self, manifest_path):
        manifest = json.loads(Path(manifest_path).read_text())
        for member in manifest['members']:
            key = member['sha256']
            if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                raise ValueError('Invalid checkpoint blob key')
            blob = self.blobs / key
            with blob.open('rb') as stream:
                if blob.stat().st_size != member['size'] or hashlib.file_digest(stream, 'sha256').hexdigest() != key:
                    raise ValueError('Checkpoint integrity failure: ' + key)
        return manifest

    def restore(self, manifest_path):
        manifest = self.verify(manifest_path)
        # Refuse to overwrite another image's current tag.
        for tag in manifest['original_tags']:
            existing = subprocess.run(self.docker + ['image', 'inspect', tag], capture_output=True, text=True)
            if existing.returncode == 0 and json.loads(existing.stdout)[0]['Id'] != manifest['image_id']:
                raise ValueError('Restore would replace a different tagged image: ' + tag)
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen(self.docker + ['image', 'load'], stdin=subprocess.PIPE, stdout=log, stderr=log)
            try:
                with tarfile.open(fileobj=proc.stdin, mode='w|') as archive:
                    for member in manifest['members']:
                        path = PurePosixPath(member['path'])
                        if path.is_absolute() or '..' in path.parts:
                            raise ValueError('Unsafe checkpoint archive path')
                        info = tarfile.TarInfo(member['path'])
                        info.size, info.mode = member['size'], member['mode']
                        with (self.blobs / member['sha256']).open('rb') as stream:
                            archive.addfile(info, stream)
                proc.stdin.close()
                if proc.wait(timeout=120):
                    log.seek(0)
                    raise RuntimeError(log.read().decode(errors='replace')[-1500:])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        result = json.loads(subprocess.check_output(self.docker + ['image', 'inspect', manifest['image_id']], text=True))[0]
        if result['Id'] != manifest['image_id']:
            raise ValueError('Restored image identity mismatch')
        return result['Id']
