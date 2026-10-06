"""Real SWE2 + upstream mini loop + isolated container acceptance gate."""
import asyncio
import argparse
import json
import time
from types import SimpleNamespace

from harbor_agent import ROOT, dump
from mini_agent import MiniSweStudy
from oneday_operator import Environment
from resource_pool import ResourcePool


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='mini-transport-smoke02')
    parser.add_argument('--condition', choices=['mini_swe', 'sg_v2'], default='mini_swe')
    args = parser.parse_args()
    target = ROOT / 'setup' / (args.label + '.json')
    if target.exists():
        raise FileExistsError(target)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    name = args.label
    start = time.monotonic()
    dump(target, {'status': 'waiting_resources', 'started_at': time.time()})
    while not pool.acquire(name, {'cpus': 1, 'memory_mb': 4096}):
        if time.monotonic() - start > 900:
            raise TimeoutError('Smoke admission timeout')
        await asyncio.sleep(3)
    if args.condition == 'mini_swe':
        agent = MiniSweStudy(ROOT / 'control', max_requests=8, max_episodes=1, budget_seconds=180)
    else:
        from mechanism_agent import MechanismStudy
        agent = MechanismStudy(ROOT / 'control', max_requests=12, max_episodes=2, budget_seconds=240)
    image = json.loads((ROOT / 'setup/oneday-assets01.json').read_text())['image_id']
    container = None
    try:
        container = await agent.docker('run', '-d', '--network', 'none', '--cpus', '1',
            '--memory', '2g', '--memory-swap', '2g', '--user', 'root', '--workdir', '/workspace',
            '--entrypoint', 'sleep', image, 'infinity')
        env = Environment(agent, container)
        await agent.setup(env)
        isolated = await env.exec('test ! -e /var/lib/supergoal-lab/private/model.json && test ! -S /var/lib/supergoal-lab/run/docker.sock && echo isolated')
        context = SimpleNamespace()
        await agent.run('Create /workspace/probe.txt containing exactly MINI_SWE_REAL_LOOP_06 followed by a newline. Read the file back to verify it, then submit.', env, context)
        result = await env.exec('cat /workspace/probe.txt')
        report = json.loads((agent.control / 'report.json').read_text())
        dump(target, {'status': 'passed' if result.stdout.strip() == 'MINI_SWE_REAL_LOOP_06'
                      and isolated.stdout.strip() == 'isolated' and report['status'] in {'agent_stopped', 'succeeded'} else 'failed',
                      'control_id': agent.uid, 'upstream_commit': report.get('upstream_commit'),
                      'condition': args.condition, 'executor_paused_during_audit': report.get('executor_paused_during_audit'),
                      'requests': report['used_requests'], 'report_status': report['status'],
                      'isolation': isolated.stdout.strip(), 'artifact_content': result.stdout.strip()})
    except BaseException as exc:
        dump(target, {'status': 'failed', 'error': type(exc).__name__ + ': ' + str(exc), 'control_id': agent.uid})
        raise
    finally:
        if container:
            await agent.docker('rm', '-f', container)
        pool.release(name)


if __name__ == '__main__':
    asyncio.run(main())
