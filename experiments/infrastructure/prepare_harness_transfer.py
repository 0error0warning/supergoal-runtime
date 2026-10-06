"""Fix three new task IDs before oracle outcomes; never replace unavailable tasks."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import tomllib

from run_registered import summarize, write_json


ROOT = Path('/var/lib/supergoal-lab')
LABEL = 'harness-transfer01'
TASKS = ['adaptive-rejection-sampler', 'polyglot-rust-c', 'gcode-to-text']
SOURCE = ROOT / 'upstream/terminal-bench-2-1'
CANDIDATE = ROOT / 'candidates/longhorizon-dev01'
DOCKER = ['docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def file_hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob('*')) if p.is_file()}


def main():
    selection_path = ROOT / 'setup' / ('selection-' + LABEL + '.json')
    receipt_path = ROOT / 'setup' / (LABEL + '-preparation.json')
    if selection_path.exists() or receipt_path.exists():
        raise FileExistsError('Selection or oracle attempt already exists')
    prior_tasks = set()
    paths = list((ROOT / 'setup').glob('registration-*.json'))
    paths += list((ROOT / 'candidates').glob('*/experiments/public_benchmarks/registration-*.json'))
    sources = {}
    for path in paths:
        data = json.loads(path.read_text())
        for task in data.get('tasks', []):
            if isinstance(task, dict) and task.get('task_id'):
                prior_tasks.add(task['task_id'])
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    for path in (ROOT / 'jobs').glob('*/*/result.json'):
        data = json.loads(path.read_text())
        if ((data.get('agent_result') or {}).get('metadata') or {}).get('control_id'):
            prior_tasks.add(data.get('task_name', '').removeprefix('terminal-bench/'))
    if set(TASKS) & prior_tasks:
        raise ValueError('Selection overlaps a previous registered or observed task: ' +
                         ', '.join(sorted(set(TASKS) & prior_tasks)))
    tasks = []
    for name in TASKS:
        path = SOURCE / 'tasks' / name / 'task.toml'
        raw = path.read_bytes()
        data = tomllib.loads(raw.decode())
        e = data['environment']
        if e['cpus'] != 1 or e['memory_mb'] > 4096 or e['gpus'] != 0:
            raise ValueError('Task outside the reserved lane')
        tasks.append({'task_id': name, 'metadata_path': path.relative_to(SOURCE).as_posix(),
            'metadata_git_blob_sha': hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest(),
            'category': data['metadata']['category'], 'difficulty': data['metadata']['difficulty'],
            'resources': {key: e[key] for key in ['cpus', 'memory_mb', 'storage_mb', 'gpus']},
            'docker_image_tag': e['docker_image'], 'agent_timeout_sec': data['agent']['timeout_sec'],
            'verifier_timeout_sec': data['verifier']['timeout_sec'],
            'files_sha256': file_hashes(path.parent)})
    selection = {'experiment': LABEL, 'selected_at': now(), 'model_calls_on_selected_tasks': 0,
        'tasks': tasks, 'prior_registration_sources': sources, 'prior_task_ids': sorted(prior_tasks),
        'selection_rule': 'Three fixed unused task IDs: scientific computing, hard software engineering, file operations; one CPU and <=4 GiB. Deliberate selection, not random population sampling.',
        'preparation_revision': 2,
        'previous_attempt': 'harness-transfer01-selection-abort01.json; overlap check stopped before registration, oracle or model calls',
        'unavailable_task_policy': 'Keep all four assigned cells unavailable; do not replace the task',
        'arms': ['native', 'sg_v2', 'mini_swe', 'longhorizon'],
        'automatic_retries': 0, 'operator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    write_json(selection_path, selection)
    receipt = {'status': 'waiting_smoke', 'model_calls': 0, 'rows': [], 'selected_at': selection['selected_at']}
    write_json(receipt_path, receipt)
    gate = ROOT / 'setup/longhorizon-smoke01.json'
    end = datetime.datetime.fromisoformat('2026-10-06T14:14:06+00:00').timestamp()
    while True:
        smoke = json.loads(gate.read_text())
        if smoke['status'] == 'passed':
            break
        if smoke['status'] != 'running' or time.time() > end - 3600:
            receipt.update(status='not_started_smoke_failed', smoke_status=smoke['status'])
            write_json(receipt_path, receipt)
            return
        time.sleep(5)
    receipt['status'] = 'running_reference_checks'
    write_json(receipt_path, receipt)
    env = dict(os.environ, DOCKER_HOST=DOCKER[2], PYTHONDONTWRITEBYTECODE='1',
        PYTHONPATH=':'.join(map(str, [CANDIDATE, CANDIDATE / 'experiments', CANDIDATE / 'experiments/public_benchmarks'])))
    for index, task in enumerate(tasks):
        row = {'task_id': task['task_id'], 'status': 'pending'}
        receipt['rows'].append(row)
        if shutil.disk_usage(ROOT).free < 60 * 1024**3 or time.time() + 2400 > end:
            row['status'] = 'not_started_resource_reserve'
            write_json(receipt_path, receipt)
            continue
        name = f"tb-oracle-{LABEL}-{index+1:02d}-{task['task_id']}"
        config_path = ROOT / 'setup' / (name + '.json')
        if config_path.exists() or (ROOT / 'jobs' / name).exists():
            raise FileExistsError('Previously started oracle')
        task_dir = SOURCE / 'tasks' / task['task_id']
        config = {'job_name': name, 'jobs_dir': str(ROOT / 'jobs'), 'n_concurrent_trials': 1,
            'environment': {'type': 'docker', 'delete': True},
            'agents': [{'import_path': 'oracle_preflight:HttpsOracle', 'kwargs': {'task_dir': str(task_dir)}}],
            'tasks': [{'path': str(task_dir)}]}
        write_json(config_path, config)
        with (ROOT / 'setup' / (name + '.log')).open('xb') as log:
            proc = subprocess.Popen([str(ROOT / 'harbor-venv/bin/harbor'), 'run', '--config', str(config_path)],
                                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
            row.update(status='running', pid=proc.pid, started_at=now(), job=str(ROOT / 'jobs' / name))
            write_json(receipt_path, receipt)
            proc.wait()
        row.update(summarize(ROOT / 'jobs' / name), exit_code=proc.returncode, finished_at=now())
        row['reference_passed'] = row['status'] == 'graded' and row['raw_rewards'] == {'reward': 1.0}
        if file_hashes(task_dir) != task['files_sha256']:
            raise ValueError('Task source changed during preflight')
        image = subprocess.run(DOCKER + ['image', 'inspect', task['docker_image_tag'], '--format', '{{.Id}}'],
                               capture_output=True, text=True)
        row['image_id'] = image.stdout.strip() if image.returncode == 0 else None
        write_json(receipt_path, receipt)
    receipt.update(status='complete_with_itemized_outcomes', finished_at=now())
    write_json(receipt_path, receipt)


if __name__ == '__main__':
    main()
