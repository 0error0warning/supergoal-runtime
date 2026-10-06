"""Actual Docker timeout/cancellation regression probe, no model calls.

Creates only named probe resources. It compares the prior implicit-pause commit
with explicit pause ownership, then confirms a verifier command can run.
"""
import asyncio
import argparse
import hashlib
import json
from pathlib import Path
import time

from snapshot_lifecycle import capture_snapshot


ROOT = Path('/var/lib/supergoal-lab')
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


class Docker:
    def __init__(self):
        self.inject = None
        self.injections = 0

    async def __call__(self, *args, timeout=60):
        proc = await asyncio.create_subprocess_exec(*DOCKER, *args,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            if args[0] == 'commit' and self.inject:
                # Real CLI and daemon operation, not a fake commit response.
                await asyncio.sleep(0.5)
                if proc.returncode is not None:
                    raise RuntimeError('Commit finished before fault activation')
                self.injections += 1
                if self.inject == 'cancel':
                    raise asyncio.CancelledError('injected caller cancellation')
                raise TimeoutError('injected commit client timeout')
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.communicate()
            raise
        if proc.returncode:
            raise RuntimeError(f'Docker {args[0]} exit {proc.returncode}: ' + stderr.decode(errors='replace')[-1000:])
        return stdout.decode().strip()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='snapshot-lifecycle-probe02')
    args = parser.parse_args()
    path = ROOT / 'setup' / (args.label + '.json')
    if path.exists():
        raise FileExistsError(path)
    receipt = {'status': 'running', 'model_calls': 0, 'rows': [],
               'source_sha256': {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                                  for f in Path(__file__).parent.glob('*.py')}}
    def write():
        path.write_text(json.dumps(receipt, indent=2))
    write()
    docker = Docker()
    # Existing pinned Alpine image; no network or package installation.
    image = await docker('image', 'inspect', 'alpine:3.22', '--format', '{{.Id}}')
    receipt['base_image'] = image
    for method, fault in [('implicit', 'timeout'), ('explicit', 'timeout'), ('explicit', 'cancel')]:
        container, tag = None, f'sg-snapshot-probe:{args.label}-{method}-{fault}'
        row = {'method': method, 'fault': fault, 'status': 'running'}
        receipt['rows'].append(row)
        write()
        try:
            container = await docker('run', '-d', '--network', 'none', '--cpus', '0.5', '--memory', '512m',
                '--label', 'supergoal.probe='+args.label, image, 'sleep', 'infinity')
            row['container_id'] = container
            await docker('exec', container, 'sh', '-c',
                'dd if=/dev/urandom of=/tmp/checkpoint-probe.bin bs=1048576 count=256 2>/dev/null', timeout=90)
            docker.inject = fault
            try:
                if method == 'implicit':
                    await docker('commit', container, tag)
                else:
                    row['capture'] = {}
                    await capture_snapshot(docker, container, tag, row['capture'])
            except (TimeoutError, asyncio.CancelledError) as exc:
                row['observed_exception'] = type(exc).__name__
            finally:
                docker.inject = None
            state = json.loads(await docker('inspect', container))[0]['State']
            row['state_after_client_failure'] = state
            try:
                row['verifier_output'] = await docker('exec', container, 'sh', '-c', 'printf VERIFIER_CAN_RUN')
                row['verifier_can_run'] = row['verifier_output'] == 'VERIFIER_CAN_RUN'
            except RuntimeError as exc:
                row.update(verifier_can_run=False, verifier_error=str(exc))
            expected = method == 'explicit'
            row['status'] = 'passed' if row['verifier_can_run'] == expected and 'observed_exception' in row else 'failed'
            # Quarantine the deliberately failed probe until its daemon commit
            # settles. A late image is never reclassified as a valid checkpoint.
            if state['Paused']:
                await docker('unpause', container)
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    row['late_untrusted_image'] = await docker('image', 'inspect', tag, '--format', '{{.Id}}')
                    break
                except RuntimeError:
                    await asyncio.sleep(1)
            if 'late_untrusted_image' not in row:
                row.update(status='requires_audit', error='Daemon commit did not settle')
        except BaseException as exc:
            row.update(status='failed', error=type(exc).__name__ + ': ' + str(exc))
            receipt['status'] = 'failed'
            if container:
                row['final_container_state'] = json.loads(await docker('inspect', container))[0]['State']
            raise
        finally:
            docker.inject = None
            if container:
                await docker('rm', '-f', container)
            if row.get('late_untrusted_image'):
                await docker('image', 'rm', tag)
            write()
    receipt.update(status='passed' if all(r['status'] == 'passed' for r in receipt['rows']) else 'failed',
                   fault_activations=docker.injections)
    write()
    print(json.dumps({'status': receipt['status'], 'fault_activations': docker.injections}))


if __name__ == '__main__':
    asyncio.run(main())
