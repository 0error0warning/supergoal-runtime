"""Finite, best-effort capture of frozen trials' transient image checkpoints.

It does not modify solvers, containers, hidden tests, or recorded trial outcomes.
Missing images remain missing; a queued capture is never called a durable backup.
"""
from concurrent.futures import ThreadPoolExecutor
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from artifact_store import DockerCheckpointStore

ROOT = Path('/var/lib/supergoal-lab')
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def main():
    path = ROOT / 'setup/checkpoint-retention01.json'
    if path.exists():
        raise FileExistsError(path)
    if json.loads((ROOT / 'setup/artifact-retention-probe02.json').read_text())['status'] != 'passed':
        raise RuntimeError('Actual image-removal/restore probe failed')
    store = DockerCheckpointStore(ROOT / 'checkpoint-store', DOCKER)
    receipt = {'status': 'running', 'capture_is_best_effort': True, 'model_calls': 0,
               'source_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                                  for f in Path(__file__).parent.glob('*.py')}, 'images': {}}
    cutoff = datetime.datetime.fromisoformat('2026-10-06T14:20:00+00:00').timestamp()
    futures = {}
    seen = set()
    def write():
        receipt['heartbeat_epoch'] = time.time()
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(receipt, indent=2))
        temp.replace(path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        while time.time() < cutoff:
            listing = subprocess.check_output(DOCKER + ['image', 'ls', '--no-trunc', '--filter',
                       'reference=sg-artifact:*', '--format', '{{.Repository}}:{{.Tag}} {{.ID}}'], text=True)
            current = dict(line.split() for line in listing.splitlines())
            # Docker emits newest images first. Complete manifests survive image
            # cleanup; until capture finishes, availability remains unproven.
            for tag, image_id in current.items():
                if image_id in seen or len(futures) >= 2:
                    continue
                if shutil.disk_usage(ROOT).free < 60 * 1024**3:
                    receipt['admission_status'] = 'disk_reserve_reached'
                    break
                seen.add(image_id)
                receipt['images'][image_id] = {'tag': tag, 'status': 'capturing', 'started': time.time()}
                futures[pool.submit(store.save, tag)] = image_id
            for future in list(futures):
                if not future.done():
                    continue
                image_id = futures.pop(future)
                row = receipt['images'][image_id]
                try:
                    manifest = future.result()
                    row.update(status='durable', manifest=str(manifest),
                               manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
                except Exception as exc:
                    row.update(status='capture_failed', error=type(exc).__name__ + ': ' + str(exc)[:900])
                row['finished'] = time.time()
            write()
            time.sleep(1)
        for future, image_id in futures.items():
            try:
                manifest = future.result()
                receipt['images'][image_id].update(status='durable', manifest=str(manifest),
                    manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest())
            except Exception as exc:
                receipt['images'][image_id].update(status='capture_failed', error=str(exc)[:900])
    receipt['status'] = 'capture_window_closed'
    write()


if __name__ == '__main__':
    main()
