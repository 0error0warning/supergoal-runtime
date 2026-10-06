"""Actual pinned Hermes/Container A-B verification, with zero model calls."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

from resource_pool import ResourcePool


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'file-tool-probe03'
IMAGE = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    target = ROOT / 'setup' / (LABEL + '.json')
    if target.exists():
        raise FileExistsError('Probe already started')
    old = ROOT / 'candidates/harness-transfer01/experiments/public_benchmarks/hermes_bridge.py'
    fixed = Path(__file__).parent / 'hermes_bridge.py'
    receipt = {'status': 'waiting_capacity', 'model_calls': 0, 'image_id': IMAGE,
               'source_sha256': {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), old, fixed]},
               'scope': 'Fixed-text tool contracts only; not a model task sample or benchmark replacement'}

    def save():
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(receipt, indent=2))
        temp.replace(target)

    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    save()
    deadline = time.monotonic() + 1200
    while not pool.acquire(LABEL, {'cpus': 1, 'memory_mb': 2048}):
        if time.monotonic() > deadline:
            receipt['status'] = 'not_started_capacity_unavailable'
            save()
            return
        time.sleep(5)
    container = None
    try:
        home = ROOT / 'control' / LABEL / 'hermes-home'
        home.mkdir(parents=True)
        os.environ['HERMES_HOME'] = str(home)
        container = subprocess.check_output(DOCKER + ['run', '-d', '--network', 'none', '--cpus', '1',
            '--memory', '1g', '--memory-swap', '1g', '--workdir', '/app', '--entrypoint', 'sleep', IMAGE, 'infinity'],
            text=True, timeout=30).strip()
        receipt.update(status='running', container_id=container)
        descriptor = {'container_id': container, 'docker_host': DOCKER[2], 'user': 'root'}
        from tools.file_operations import ShellFileOperations

        class Captured(load(old, 'old_hermes_bridge').HarborTaskEnvironment):
            def execute(self, command, *args, **kwargs):
                if kwargs.get('stdin_data'):
                    self.last_stdin_command = command
                return super().execute(command, *args, **kwargs)

        legacy = Captured(cwd='/app', timeout=20, descriptor=descriptor)
        old_ops = ShellFileOperations(legacy)
        receipt['old_write'] = old_ops.write_file('/app/old.txt', 'old-content\n').to_dict()
        receipt['old_readback'] = subprocess.check_output(DOCKER + ['exec', container, 'cat', '/app/old.txt'],
                                                        text=True, timeout=20)
        atomic = legacy.last_stdin_command
        legacy.execute('export SG_TOOL_PROBE_STATE=retained')
        receipt['old_export'] = legacy.execute('printf %s "${SG_TOOL_PROBE_STATE:-missing}"')
        trace = subprocess.run(DOCKER + ['exec', '-i', container, 'bash', '-lx', '-c', legacy._wrap_command(atomic, '/app')],
                               input='old-content\n', capture_output=True, text=True, timeout=20)
        receipt['login_trace'] = {'returncode': trace.returncode, 'stdout': trace.stdout, 'stderr': trace.stderr}
        save()
        corrected = load(fixed, 'fixed_hermes_bridge').HarborTaskEnvironment(cwd='/app', timeout=20, descriptor=descriptor)
        receipt['snapshot_ready'] = corrected._snapshot_ready
        ops = ShellFileOperations(corrected)
        payload = 'Unicode: 中文\n' + ('x' * 200000) + '\n'
        receipt['fixed_write'] = ops.write_file('/app/space folder/fixed.txt', payload).to_dict()
        sha = hashlib.sha256(payload.encode()).hexdigest()
        receipt['expected_sha256'] = sha
        disk = subprocess.check_output(DOCKER + ['exec', container, 'sha256sum', '/app/space folder/fixed.txt'],
                                       text=True, timeout=20).split()[0]
        receipt['observed_sha256'] = disk
        corrected.execute('export SG_TOOL_PROBE_STATE=retained')
        receipt['fixed_export'] = corrected.execute('printf %s "${SG_TOOL_PROBE_STATE:-missing}"')
        receipt['real_command_failure'] = corrected.execute('set -e; false')
        receipt['denied_write'] = ops.write_file('/proc/supergoal-fixture-denied', 'forbidden').to_dict()
        receipt['isolation_check'] = corrected.execute(
            'test ! -e /var/lib/supergoal-lab/private/model.json && test ! -S /var/lib/supergoal-lab/run/docker.sock')
        good = (bool(receipt['old_write'].get('error')) and receipt['old_readback'] == 'old-content\n'
                and receipt['old_export']['output'] == 'missing'
                and receipt['snapshot_ready'] and not receipt['fixed_write'].get('error') and disk == sha
                and receipt['fixed_export']['output'] == 'retained'
                and receipt['real_command_failure']['returncode'] != 0 and bool(receipt['denied_write'].get('error'))
                and receipt['isolation_check']['returncode'] == 0)
        receipt['status'] = 'passed' if good else 'failed'
    except BaseException as exc:
        receipt.update(status='probe_error', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        if container:
            try:
                done = subprocess.run(DOCKER + ['rm', '-f', container], capture_output=True, text=True, timeout=30)
                receipt['cleanup_exit'] = done.returncode
            except Exception as exc:
                receipt['cleanup_error'] = str(exc)
        if not container or receipt.get('cleanup_exit') == 0:
            pool.release(LABEL)
        else:
            receipt['status'] = 'cleanup_requires_audit; reservation retained'
        save()


if __name__ == '__main__':
    main()
