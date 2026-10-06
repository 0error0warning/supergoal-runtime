"""Finite, separately registered real-model gate for the upstream role loop."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from harbor_agent import ROOT, dump
from longhorizon_agent import LongHorizonStudy


LABEL = 'longhorizon-smoke01'
IMAGE = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'


class Environment:
    def __init__(self, agent, container):
        self.agent, self.container = agent, container

    async def exec(self, command, timeout_sec=30, user='root'):
        proc = await asyncio.create_subprocess_exec('/usr/bin/docker', '-H', self.agent.docker_host,
            'exec', '--user', user, self.container, 'timeout', '--signal=TERM', '--kill-after=3s',
            str(timeout_sec), 'bash', '-c', command,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout_sec + 10)
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
        return SimpleNamespace(return_code=proc.returncode, stdout=stdout.decode(), stderr=stderr.decode())


async def main():
    target = ROOT / 'setup' / (LABEL + '.json')
    if target.exists():
        raise FileExistsError('Already started; do not retry')
    registration = ROOT / 'setup' / ('registration-' + LABEL + '.json')
    reg = json.loads(registration.read_text())
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != reg['operator_sha256']:
        raise ValueError('Registered smoke operator changed')
    candidate = Path(reg['candidate'])
    for name, sha in reg['candidate_source_sha256'].items():
        if hashlib.sha256((candidate / name).read_bytes()).hexdigest() != sha:
            raise ValueError('Registered candidate changed: ' + name)
    state = subprocess.check_output(['systemctl', 'show', 'supergoal-mechanism01.service',
                                    '--property=ActiveState', '--value'], text=True).strip()
    if state not in {'inactive', 'failed'} or shutil.disk_usage(ROOT).free < 60 * 1024**3:
        raise RuntimeError('Reserved lane or disk reserve unavailable')
    agent = LongHorizonStudy(ROOT / 'control', max_requests=32, max_episodes=4, budget_seconds=360)
    receipt = {'status': 'running', 'control_id': agent.uid, 'model': 'devin/swe-2',
               'registration_sha256': hashlib.sha256(registration.read_bytes()).hexdigest(),
               'scope': 'SDK/container/upstream-loop integration only; not a public benchmark sample'}
    dump(target, receipt)
    container = None
    try:
        container = await agent.docker('run', '-d', '--network', 'none', '--cpus', '1',
            '--memory', '2g', '--memory-swap', '2g', '--workdir', '/app',
            '--entrypoint', 'sleep', IMAGE, 'infinity')
        env = Environment(agent, container)
        await agent.setup(env)
        context = SimpleNamespace()
        await agent.run('Create /app/probe.json containing exactly the JSON object '
            '{"marker":"LH_REAL_ROLE_LOOP_01","square":1521}. Read it back and verify both values before finishing.',
            env, context)
        observed = await env.exec("python3 -c 'import json;assert json.load(open(\"/app/probe.json\"))=={\"marker\":\"LH_REAL_ROLE_LOOP_01\",\"square\":1521};print(\"accepted\")'")
        report = json.loads((agent.control / 'report.json').read_text())
        roles = [r.get('longhorizon_role') for r in report['rounds']]
        receipt.update(status='passed' if observed.return_code == 0 and report['status'] == 'succeeded'
                       and {'manager', 'cli_executor', 'cli_auditor'}.issubset(roles) else 'failed',
                       requests=report['used_requests'], report_status=report['status'], role_sequence=roles,
                       checker_exit=observed.return_code, checker_output=observed.stdout,
                       upstream_result=report.get('upstream_result'),
                       environment=json.loads((agent.control / 'environment.json').read_text()))
    except BaseException as exc:
        receipt.update(status='failed', error=type(exc).__name__ + ': ' + str(exc)[:1800])
        raise
    finally:
        if container:
            try:
                await agent.docker('rm', '-f', container)
            except Exception as exc:
                receipt.update(status='failed_cleanup', cleanup_error=str(exc)[:900])
        dump(target, receipt)


if __name__ == '__main__':
    asyncio.run(main())
