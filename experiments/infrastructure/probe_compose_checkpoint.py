"""Check actual Compose cleanup, durable archival and exact image restoration."""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

from artifact_store import DockerCheckpointStore
from probe_snapshot_lifecycle import Docker, DOCKER, ROOT
from snapshot_lifecycle import capture_snapshot


async def main():
    path = ROOT / 'setup/compose-retention-probe04.json'
    if path.exists():
        raise FileExistsError(path)
    work = ROOT / 'probes/compose-retention04'
    work.mkdir(exist_ok=False)
    docker = Docker()
    base = json.loads(await docker('image', 'inspect', 'alpine:3.22'))[0]['RepoDigests'][0]
    (work / 'Dockerfile').write_text('FROM ' + base + '\nRUN printf DETACHED_CHECKPOINT_04 > /probe.txt\n')
    compose = work / 'compose.yaml'
    compose.write_text('services:\n  main:\n    build: .\n    command: ["sleep", "infinity"]\n'
                       '    cpus: 0.5\n    mem_limit: 128m\n    network_mode: none\n')
    prefix = ['compose', '--project-name', 'sg-retention-diagnostic04', '--file', str(compose)]
    tag = 'sg-retention-probe:compose04'
    out = {'status': 'running', 'model_calls': 0, 'base_image': base, 'capture': {},
           'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in Path(__file__).parent.glob('*.py')}}
    path.write_text(json.dumps(out))
    restored_container = None
    try:
        await docker(*prefix, 'up', '--build', '-d')
        container = await docker(*prefix, 'ps', '-q', 'main')
        image_id = await capture_snapshot(docker, container, tag, out['capture'])
        out['cleanup_output'] = await docker(*prefix, 'down', '--rmi', 'local', '--volumes', '--remove-orphans')
        info = json.loads(await docker('image', 'inspect', image_id))[0]
        out['image_survived_cleanup'] = info['Id'] == image_id
        out['labels_after_capture'] = info['Config']['Labels']
        store = DockerCheckpointStore(ROOT / 'checkpoint-store', DOCKER)
        manifest = await asyncio.to_thread(store.save, tag)
        out['manifest'] = str(manifest)
        out['manifest_sha256'] = hashlib.sha256(manifest.read_bytes()).hexdigest()
        await docker('image', 'rm', tag)
        missing = subprocess.run(DOCKER + ['image', 'inspect', image_id], capture_output=True).returncode != 0
        restored = await asyncio.to_thread(store.restore, manifest)
        restored_container = await docker('create', '--network', 'none', '--entrypoint', '/bin/false', restored)
        content = work / 'restored-content.txt'
        await docker('cp', restored_container + ':/probe.txt', str(content))
        out.update(image_absent_before_restore=missing, restored_image_id=restored,
                   restored_content=content.read_text(), status='passed' if missing and restored == image_id
                   and content.read_text() == 'DETACHED_CHECKPOINT_04' else 'failed')
    except BaseException as exc:
        out.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        subprocess.run(DOCKER + prefix + ['down', '--rmi', 'local', '--volumes', '--remove-orphans'], capture_output=True)
        if restored_container:
            subprocess.run(DOCKER + ['rm', '-f', restored_container], capture_output=True)
        subprocess.run(DOCKER + ['image', 'rm', tag], capture_output=True)
        path.write_text(json.dumps(out, indent=2))
    print(json.dumps({'status': out['status'], 'image_survived_cleanup': out['image_survived_cleanup']}))


if __name__ == '__main__':
    asyncio.run(main())
