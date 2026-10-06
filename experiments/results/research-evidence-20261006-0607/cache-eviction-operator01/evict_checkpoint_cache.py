"""Evict only unused image caches backed by freshly verified durable archives."""
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import time


ROOT = Path('/var/lib/supergoal-lab')
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def main():
    plan_path = ROOT / 'setup/checkpoint-cache-eviction01-priority.json'
    receipt_path = ROOT / 'setup/checkpoint-cache-eviction01.json'
    if receipt_path.exists():
        raise FileExistsError('Eviction attempt already exists')
    plan = json.loads(plan_path.read_text())
    receipt = {'status': 'running', 'started_epoch': time.time(), 'rows': [],
               'free_bytes_before': shutil.disk_usage(ROOT).free,
               'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
               'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               'archive_blobs_deleted': 0, 'forced_removals': 0}
    verified = {}

    def save():
        receipt['free_bytes_observed'] = shutil.disk_usage(ROOT).free
        temp = receipt_path.with_suffix('.tmp')
        temp.write_text(json.dumps(receipt, indent=2))
        temp.replace(receipt_path)

    save()
    try:
        for candidate in plan['candidates']:
            if shutil.disk_usage(ROOT).free >= plan['target_free_gib'] * 2**30:
                break
            row = {**candidate, 'status': 'verifying_archive'}
            receipt['rows'].append(row)
            save()
            path = Path(candidate['manifest']).resolve(strict=True)
            if path.parent != ROOT / 'checkpoint-store/manifests':
                raise ValueError('Manifest outside owned archive')
            if hashlib.sha256(path.read_bytes()).hexdigest() != candidate['manifest_sha256']:
                raise ValueError('Manifest changed')
            manifest = json.loads(path.read_text())
            if manifest['image_id'] != candidate['image_id']:
                raise ValueError('Archive image identity mismatch')
            for member in manifest['members']:
                key = member['sha256']
                if not re.fullmatch(r'[0-9a-f]{64}', key):
                    raise ValueError('Invalid content address')
                blob = ROOT / 'checkpoint-store/blobs' / key
                stat = blob.stat()
                signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if stat.st_size != member['size']:
                    raise ValueError('Archive member size changed')
                if verified.get(key) != signature:
                    with blob.open('rb') as file:
                        if hashlib.file_digest(file, 'sha256').hexdigest() != key:
                            raise ValueError('Archive member hash changed')
                    if blob.stat().st_mtime_ns != stat.st_mtime_ns:
                        raise ValueError('Archive changed during verification')
                    verified[key] = signature
            row['archive_verified_epoch'] = time.time()
            ids = subprocess.check_output(DOCKER + ['ps', '-aq'], text=True, timeout=20).split()
            containers = json.loads(subprocess.check_output(DOCKER + ['inspect', *ids], text=True, timeout=20)) if ids else []
            if candidate['image_id'] in {c['Image'] for c in containers}:
                row['status'] = 'skipped_container_reference'
                save()
                continue
            inspected = subprocess.run(DOCKER + ['image', 'inspect', candidate['image_id']],
                                       capture_output=True, text=True, timeout=20)
            if inspected.returncode:
                row['status'] = 'cache_already_absent'
                save()
                continue
            info = json.loads(inspected.stdout)[0]
            if (info['Id'] != candidate['image_id'] or info.get('RepoTags') != [candidate['tag']]
                    or not re.fullmatch(r'sg-artifact:[0-9a-f]{32}', candidate['tag'])):
                raise ValueError('Image identity or tag ownership changed')
            row['status'] = 'removing_verified_cache'
            save()
            result = subprocess.run(DOCKER + ['image', 'rm', '--no-prune', candidate['tag']],
                                    capture_output=True, text=True, timeout=60)
            row.update(status='cache_evicted' if result.returncode == 0 else 'removal_refused',
                       exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr,
                       completed_epoch=time.time())
            save()
        receipt.update(status='complete', finished_epoch=time.time(), verified_blob_count=len(verified))
    except BaseException as exc:
        receipt.update(status='stopped_requires_audit', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        save()


if __name__ == '__main__':
    main()
