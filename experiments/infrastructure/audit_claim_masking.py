"""Paired audits of fixed pre-verifier artifacts; hidden scores never enter prompts."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import time

from harbor_agent import HermesSupergoalStudy, ROOT, dump
from model_proxy import ModelProxy
from resource_pool import ResourcePool


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    args = parser.parse_args()
    reg = json.loads(args.registration.read_text())
    for relative, expected in reg['source_sha256'].items():
        if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected:
            raise RuntimeError('Frozen audit apparatus changed: ' + relative)
    receipt_path = ROOT / 'setup/audit-claim-masking01.json'
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    receipt = {'status': 'running', 'rows': [], 'registration_sha256': hashlib.sha256(args.registration.read_bytes()).hexdigest(),
               'cases': len(reg['cases']), 'distinct_tasks': len({c['task_id'] for c in reg['cases']})}
    dump(receipt_path, receipt)
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    for case in reg['cases']:
        for condition in case['order']:
            if not case['artifact_available_at_registration']:
                receipt['rows'].append({'case_id': case['case_id'], 'task_id': case['task_id'],
                                       'condition': condition, 'status': 'artifact_unavailable',
                                       'expected_image_id': case['artifact_image_id']})
                dump(receipt_path, receipt)
                continue
            name = f"audit-claim-masking01-{case['case_id']}-{condition}"
            while not pool.acquire(name, {'cpus': 1, 'memory_mb': 4096}):
                if time.time() > reg['stop_admission_epoch'] - 900:
                    receipt['status'] = 'admission_closed'
                    dump(receipt_path, receipt)
                    return
                await asyncio.sleep(5)
            original = ROOT / 'control' / case['control_id']
            report = json.loads((original / 'report.json').read_text())
            environment = json.loads((original / 'environment.json').read_text())
            instruction = (ROOT / 'upstream/terminal-bench-2-1/tasks' / case['task_id'] / 'instruction.md').read_text()
            assert hashlib.sha256(instruction.encode()).hexdigest() == case['instruction_sha256']
            assert report['artifact_image_id'] == case['artifact_image_id']
            agent = HermesSupergoalStudy(ROOT / 'control', max_requests=16, max_episodes=1, budget_seconds=600)
            agent.cwd, agent.user = environment['working_dir'], environment['execution_user']
            agent.review_memory = str(max(2048*1024**2, environment['memory_limit_bytes']))
            agent.deadline = time.monotonic() + 600
            row = {'case_id': case['case_id'], 'task_id': case['task_id'], 'condition': condition,
                   'control_id': agent.uid, 'official_reward': case['official_reward'],
                   'live_state_required': case['live_state_required'], 'status': 'running'}
            receipt['rows'].append(row)
            dump(receipt_path, receipt)
            holder = None
            try:
                holder = await agent.docker('run', '-d', '--cpus', '1', '--memory', '128m', '--memory-swap', '128m',
                    '--entrypoint', '/bin/sh', case['artifact_image_id'], '-c', 'sleep infinity')
                agent.container = holder
                await agent.docker('pause', holder)
                provider = json.loads((ROOT / 'private/model.json').read_text())
                with ModelProxy(provider, limit=16, journal=agent.control / 'request-journal.json') as proxy:
                    answer = report.get('last_executor_claim', '') if condition == 'claim_visible' else ''
                    verdict = await agent.review(instruction, answer, proxy)
                    row.update(status='reviewed', verdict=verdict, requests=len(proxy.records),
                               false_acceptance=verdict['verdict']=='pass' and case['official_reward']==0,
                               false_rejection=verdict['verdict']=='fail' and case['official_reward']==1)
                    dump(agent.control / 'calibration-report.json', {**row, 'rounds': agent.rounds, 'requests': proxy.records})
            except Exception as exc:
                row.update(status='review_error', error=type(exc).__name__ + ': ' + str(exc)[:1000])
            finally:
                if holder:
                    await agent.docker('rm', '-f', holder)
                pool.release(name)
                dump(receipt_path, receipt)
    receipt['status'] = 'complete_with_itemized_outcomes'
    dump(receipt_path, receipt)


if __name__ == '__main__':
    asyncio.run(main())
