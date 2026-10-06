"""Finite AgentIF-OneDay transfer study with a blinded, separate final judge."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import time
from types import SimpleNamespace

from harbor_agent import HermesSupergoalStudy, ROOT, dump
from model_proxy import ModelProxy
from resource_pool import ResourcePool, reservation
from swe2_request import request


UPSTREAM = ROOT / 'upstream/onedayagent'
BENCH = UPSTREAM / 'tasks/agentif_oneday'
HERE = Path(__file__).resolve().parent


class Environment:
    def __init__(self, agent, container):
        self.agent, self.container = agent, container

    async def exec(self, command, timeout_sec=30, user='root'):
        output = await self.agent.docker('exec', '--user', user, self.container,
                                        'bash', '-c', command, timeout=timeout_sec + 5)
        return SimpleNamespace(stdout=output, stderr='', return_code=0)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def judge(agent, question_id, answer, submission, files, control, *, memory_gb=4):
    if not 1 <= memory_gb <= 16:
        raise ValueError('Judge memory must have a bounded host reservation')
    control.mkdir(exist_ok=False)
    dump(control / 'config.json', {'question_id': question_id, 'answer': answer, 'files': files})
    image = json.loads((ROOT / 'setup/oneday-assets01.json').read_text())['image_id']
    container = await agent.docker('create', '--network', 'none', '--cpus', '1', '--memory', f'{memory_gb}g',
        '--memory-swap', f'{memory_gb}g', '--pids-limit', '512', '--user', 'root', '--workdir', '/judge-control',
        '--env', 'PYTHONPATH=/upstream/src', '--env', 'PYTHONDONTWRITEBYTECODE=1',
        '--mount', f'type=bind,src={UPSTREAM},dst=/upstream,readonly',
        '--mount', f'type=bind,src={BENCH},dst=/bench,readonly',
        '--mount', f'type=bind,src={submission.parent},dst=/answers,readonly',
        '--mount', f'type=bind,src={control},dst=/judge-control',
        '--mount', f'type=bind,src={HERE / "oneday_judge.py"},dst=/judge.py,readonly',
        '--entrypoint', 'python', image, '/judge.py')
    provider = json.loads((ROOT / 'private/model.json').read_text())
    started, sent = time.monotonic(), False
    try:
        await agent.docker('start', container)
        with ModelProxy(provider, limit=1, journal=control / 'model-journal.json') as proxy:
            while True:
                req = control / 'request.json'
                if req.exists() and not sent:
                    sent = True
                    payload = json.loads(req.read_text())
                    try:
                        response = await asyncio.to_thread(request, proxy.config(), payload['content'])
                    except Exception as exc:
                        response = {'error': f'{type(exc).__name__}: {exc}'[:700]}
                    dump(control / 'response.json', response)
                state = json.loads(await agent.docker('inspect', container))[0]['State']
                if not state['Running']:
                    break
                if time.monotonic() - started > 1200:
                    raise TimeoutError('Bounded final artifact evaluation exceeded 20 minutes')
                await asyncio.sleep(2)
        log = await agent.docker('logs', container)
        (control / 'stdout.log').write_text(log)
        if state['ExitCode'] or not (control / 'scores.json').exists():
            raise RuntimeError('Official artifact pipeline failed; preserve judge logs, no automatic regrade')
        scores = json.loads((control / 'scores.json').read_text())
        aggregate = json.loads((control / 'aggregate.json').read_text())
        return {'status': 'adapted_judge_graded', 'score': aggregate['total_score'],
                'max_score': aggregate['max_score'], 'percentage': aggregate['percentage'],
                'criteria': len(scores), 'judge_model': 'devin/swe-2',
                'official_gemini_score_reproduced': False, 'judge_saw_arm': False}
    finally:
        inspection = json.loads(await agent.docker('inspect', container))[0]
        dump(control / 'container-state.json', {'state': inspection['State'],
             'memory_limit_bytes': inspection['HostConfig']['Memory'],
             'container_id': container, 'image_id': inspection['Image']})
        # Include stderr in the preserved log even when dependency/rendering fails.
        proc = await asyncio.create_subprocess_exec('/usr/bin/docker', '--host', agent.docker_host, 'logs', container,
                     stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        output, _ = await proc.communicate()
        (control / 'full.log').write_bytes(output)
        await agent.docker('rm', '-f', container)


async def trial(row, reg, receipt_path):
    task = next(t for t in reg['tasks'] if t['task_id'] == row['task_id'])
    name = f"oneday-{reg['experiment']}-{row['index']:02d}"
    agent = HermesSupergoalStudy(ROOT / 'control', arm=row['arm'], max_requests=reg['max_requests'],
                                max_episodes=reg['max_episodes'], budget_seconds=reg['budget_seconds'])
    image = reg['image_id']
    container = await agent.docker('run', '-d', '--cpus', '1', '--memory', '4g', '--memory-swap', '4g',
                                  '--pids-limit', '512', '--workdir', '/workspace', '--entrypoint', '/bin/sh',
                                  '--label', 'supergoal.oneday='+name, image, '-c', 'sleep infinity')
    row.update(control_id=agent.uid, container_id=container, started_at=time.time(), status='running')
    dump(receipt_path, reg['receipt'])
    target = ROOT / 'oneday-artifacts' / name
    target.mkdir(parents=True, exist_ok=False)
    try:
        for att in task['attachments']:
            source = BENCH / 'Attachments/Questions' / att['filename']
            if sha(source) != att['sha256']:
                raise ValueError('Public input changed after registration')
            await agent.docker('cp', source, container + ':/workspace/' + att['filename'])
        env = Environment(agent, container)
        await agent.setup(env)
        instruction = task['description'] + '\n\nInput files are in /workspace with these exact names:\n'
        instruction += '\n'.join(a['filename'] for a in task['attachments'])
        instruction += ('\nCreate the requested deliverables in /workspace. Use the installed office/browser tools as needed. '
                        'For external interactions, use only the provided local simulators and public read-only websites. '
                        'If the request asks for a platform message, return its text rather than sending it to anyone. '
                        'State the paths of your final deliverables in your final response.')
        context = SimpleNamespace()
        await agent.run(instruction, env, context)
        report = json.loads((agent.control / 'report.json').read_text())
        row.update(study_status=report['status'], requests=report['used_requests'])
        await agent.docker('stop', '--time', '3', container)
        archive = target / 'workspace.tar'
        with archive.open('xb') as handle:
            proc = await asyncio.create_subprocess_exec('/usr/bin/docker', '--host', agent.docker_host,
                    'cp', container + ':/workspace/.', '-', stdout=handle, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode:
                raise RuntimeError(stderr.decode()[-700:])
        submission = target / 'submission'
        submission.mkdir()
        with tarfile.open(archive) as bundle:
            members = bundle.getmembers()
            if sum(m.size for m in members) > 2 * 1024**3 or len(members) > 20000:
                raise RuntimeError('Artifact export exceeds registered size limit')
            bundle.extractall(submission, filter='data')
        inputs = {a['filename']: a['sha256'] for a in task['attachments']}
        files = []
        for p in sorted(submission.rglob('*')):
            if p.is_symlink() or not p.is_file():
                continue
            name_in_submission = p.relative_to(submission).as_posix()
            if name_in_submission.startswith(('.git/', '.cache/', 'node_modules/', '.venv/')):
                continue
            if sha(p) != inputs.get(name_in_submission):
                files.append(name_in_submission)
        dump(target / 'artifact-manifest.json', {'archive_sha256': sha(archive),
             'files': [{'path': n, 'sha256': sha(submission/n), 'bytes': (submission/n).stat().st_size} for n in files]})
        row['artifacts'] = len(files)
        row['final_judge'] = await judge(agent, task['task_id'], report.get('last_executor_claim', ''),
                                        submission, files, target / 'judge')
        row['status'] = 'finished'
    except Exception as exc:
        row.update(status='requires_audit', error=f'{type(exc).__name__}: {exc}'[:1400])
    finally:
        row['finished_at'] = time.time()
        dump(receipt_path, reg['receipt'])
        await agent.docker('rm', '-f', container)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--registration', type=Path, required=True)
    args = parser.parse_args()
    reg = json.loads(args.registration.read_text())
    for name, expected in reg['source_sha256'].items():
        if sha(ROOT / name) != expected:
            raise ValueError('Frozen OneDay operator changed: ' + name)
    if sha(BENCH / 'datasets/questions_all.jsonl') != reg['dataset_sha256']:
        raise ValueError('OneDay dataset changed after registration')
    calibration = json.loads((ROOT / 'setup' / reg['judge_calibration_receipt']).read_text())
    if calibration.get('status') != 'passed':
        raise RuntimeError('Final artifact judge has not passed development calibration')
    receipt_path = ROOT / 'setup' / (reg['experiment'] + '-receipt.json')
    if receipt_path.exists():
        raise FileExistsError('Never automatically retry a started OneDay study')
    reg['receipt'] = {'status': 'running', 'started_at': time.time(),
                      'registration_sha256': sha(args.registration),
                      'rows': [{'index': i, **r, 'status': 'pending'} for i, r in enumerate(reg['planned_order'])]}
    dump(receipt_path, reg['receipt'])
    deadline = reg['cloud_stop_epoch'] - 600
    pool = ResourcePool(ROOT / 'run/parallel-resource-pool.json')
    pending = list(reg['receipt']['rows'])
    running = {}
    reg['receipt']['status'] = 'running'
    while pending or running:
        preflight_done = json.loads((ROOT/'setup/tb-oracle-parallel24-receipt.json').read_text())['status'].startswith('complete')
        # During TB's independent reference checks, reserve their four CPUs
        # plus two for the older serial cohort. One OneDay job uses at most two.
        local_limit = 2 if preflight_done else 1
        for row in list(pending):
            if len(running) >= local_limit:
                break
            if time.time() + reg['budget_seconds'] + 1500 >= deadline:
                row['status'] = 'not_started_admission_closed'
                pending.remove(row)
                continue
            if shutil.disk_usage(ROOT).free < 40 * 1024**3:
                row['status'] = 'not_started_disk_reserve'
                pending.remove(row)
                continue
            name = f"oneday-{reg['experiment']}-{row['index']:02d}"
            # Final judge needs 4 GiB; active executor has stopped by then.
            claim = reservation({'resources': {'cpus': 1, 'memory_mb': 4096}}, row['arm'])
            if pool.acquire(name, claim):
                running[asyncio.create_task(trial(row, reg, receipt_path))] = name
                pending.remove(row)
        for future in list(running):
            if future.done():
                name = running.pop(future)
                pool.release(name)
                future.result()
        reg['receipt']['heartbeat_at'] = time.time()
        dump(receipt_path, reg['receipt'])
        if pending or running:
            await asyncio.sleep(5)
    reg['receipt']['status'] = 'complete_with_itemized_outcomes'
    dump(receipt_path, reg['receipt'])


if __name__ == '__main__':
    asyncio.run(main())
