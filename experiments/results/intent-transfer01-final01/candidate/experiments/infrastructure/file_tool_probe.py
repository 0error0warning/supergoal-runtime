"""No-model diagnostic for write failures observed in saved Hermes trajectories."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from resource_pool import ResourcePool


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'file-tool-probe02'
IMAGE = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def main():
    path = ROOT / 'setup' / (LABEL + '.json')
    if path.exists():
        raise FileExistsError('This diagnostic already started')
    data = {'status': 'waiting_capacity', 'model_calls': 0, 'image_id': IMAGE,
            'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'scope': 'One disposable fixed-text container; no benchmark rerun or production changes', 'calls': []}

    def save():
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(data, indent=2))
        temp.replace(path)

    save()
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    wait_until = time.monotonic() + 1200
    while not pool.acquire(LABEL, {'cpus': 1, 'memory_mb': 2048}):
        if time.monotonic() > wait_until:
            data['status'] = 'not_started_capacity_unavailable'
            save()
            return
        time.sleep(5)
    container = None
    try:
        data['status'] = 'running'
        home = ROOT / 'control' / LABEL / 'hermes-home'
        home.mkdir(parents=True)
        os.environ['HERMES_HOME'] = str(home)
        container = subprocess.check_output(DOCKER + ['run', '-d', '--network', 'none', '--cpus', '1',
            '--memory', '1g', '--memory-swap', '1g', '--workdir', '/app', '--entrypoint', 'sleep', IMAGE, 'infinity'],
            text=True, timeout=30).strip()
        from hermes_bridge import HarborTaskEnvironment
        from tools.file_operations import ShellFileOperations

        class ObservedEnvironment(HarborTaskEnvironment):
            def execute(self, command, *args, **kwargs):
                result = super().execute(command, *args, **kwargs)
                data['calls'].append({'command_sha256': hashlib.sha256(command.encode()).hexdigest(),
                    'command': command, 'stdin_bytes': len((kwargs.get('stdin_data') or '').encode()),
                    'result': result})
                save()
                return result

        env = ObservedEnvironment(cwd='/app', timeout=20, descriptor={
            'container_id': container, 'docker_host': DOCKER[2], 'user': 'root'})
        ops = ShellFileOperations(env)
        data['direct_stdin'] = env.execute('cat > /app/direct.txt', stdin_data='direct-stdin\n')
        atomic = ops._atomic_write('/app/atomic.txt', 'atomic-stdin\n')
        data['atomic_write'] = {'stdout': atomic.stdout, 'exit_code': atomic.exit_code}
        data['write_file'] = ops.write_file('/app/write.txt', 'write-file\n').to_dict()
        atomic_command = next(c['command'] for c in data['calls']
                              if c['stdin_bytes'] and 'atomic.txt' in c['command'])
        raw = subprocess.run(DOCKER + ['exec', '-i', container, 'bash', '-c', atomic_command],
                             input='atomic-stdin\n', capture_output=True, text=True, timeout=20)
        data['unwrapped_atomic'] = {'returncode': raw.returncode, 'stdout': raw.stdout, 'stderr': raw.stderr}
        data['subshell_atomic'] = env.execute('(' + atomic_command + ')', stdin_data='atomic-stdin\n')
        trace = subprocess.run(DOCKER + ['exec', '-i', container, 'bash', '-x', '-c',
                                        env._wrap_command(atomic_command, '/app')],
                               input='atomic-stdin\n', capture_output=True, text=True, timeout=20)
        data['wrapped_shell_trace'] = {'returncode': trace.returncode, 'stdout': trace.stdout, 'stderr': trace.stderr}
        data['readback'] = subprocess.check_output(DOCKER + ['exec', container, 'python3', '-c',
            'import json,pathlib; print(json.dumps({n:pathlib.Path("/app",n).read_text() if pathlib.Path("/app",n).exists() else None for n in ["direct.txt","atomic.txt","write.txt"]}))'],
            text=True, timeout=15).strip()
        data['status'] = 'diagnostic_complete'
    except BaseException as exc:
        data.update(status='diagnostic_error', error=type(exc).__name__ + ': ' + str(exc))
        raise
    finally:
        if container:
            try:
                cleaned = subprocess.run(DOCKER + ['rm', '-f', container], capture_output=True, text=True, timeout=30)
                data['cleanup_exit'] = cleaned.returncode
            except Exception as exc:
                data['cleanup_error'] = type(exc).__name__ + ': ' + str(exc)
        if not container or data.get('cleanup_exit') == 0:
            pool.release(LABEL)
        else:
            data['status'] = 'cleanup_requires_audit; reservation retained'
        save()


if __name__ == '__main__':
    main()
