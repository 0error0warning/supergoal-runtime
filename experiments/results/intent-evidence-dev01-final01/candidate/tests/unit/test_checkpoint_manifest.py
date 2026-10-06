import hashlib
import json

import pytest

from experiments.infrastructure.artifact_store import DockerCheckpointStore


def test_corruption_cannot_become_a_verified_checkpoint(tmp_path):
    store = DockerCheckpointStore(tmp_path, ['unused-docker'])
    payload = b'checkpoint bytes'
    digest = hashlib.sha256(payload).hexdigest()
    blob = store.blobs / digest
    blob.write_bytes(payload)
    manifest = store.manifests / 'test.json'
    manifest.write_text(json.dumps({'members': [{'sha256': digest, 'size': len(payload)}]}))
    store.verify(manifest)
    blob.write_bytes(b'X' * len(payload))
    with pytest.raises(ValueError, match='integrity'):
        store.verify(manifest)


def test_blob_reference_cannot_escape_the_store(tmp_path):
    store = DockerCheckpointStore(tmp_path, ['unused-docker'])
    manifest = store.manifests / 'test.json'
    manifest.write_text(json.dumps({'members': [{'sha256': '../private/key', 'size': 1}]}))
    with pytest.raises(ValueError, match='blob key'):
        store.verify(manifest)
