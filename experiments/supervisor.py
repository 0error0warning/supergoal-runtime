"""Server-only serial study supervisor. Run with a capped transient systemd unit.

Production code/venv are mounted read-only; work is uid 993 with no capabilities.
Host secrets and hidden expected outputs are never mounted in agent sandboxes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from model_proxy import ModelProxy
from tasks import ARMS, FAMILIES, build

ROOT = Path('/var/lib/supergoal-lab')
BUNDLES = Path(os.environ.get('SUPERGOAL_STUDY_BUNDLES', ROOT / 'bundles'))
HOST = Path('/var/lib/upi-hermes/.hermes')
RELEASE = HOST / 'releases/hermes-v0.21.3-251bedc-mt2'
VENV = HOST / 'venvs/hermes-v0213-251bedc-mt2'
PYTHON = HOST / 'runtimes/cpython-3.11.16-ee7b3697-v0.21.3-251bedc-mt2'
UID, GID = 993, 984


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def sandbox(run, arm, *, grade=False, check=False, episode=1):
    cmd = ['/usr/bin/bwrap', '--unshare-pid', '--unshare-ipc', '--unshare-uts', '--unshare-cgroup', '--die-with-parent', '--new-session']
    if grade or check:
        cmd += ['--unshare-net']
    for path in ('/opt', '/etc', '/etc/ssl', '/home', '/run'):
        cmd += ['--perms', '0755', '--dir', path]
    for path in ('/usr', '/bin', '/lib', '/lib64', '/etc/ssl/certs', '/etc/resolv.conf', '/etc/hosts', '/etc/nsswitch.conf'):
        cmd += ['--ro-bind', path, path]
    cmd += ['--proc', '/proc', '--dev', '/dev', '--perms', '1777', '--tmpfs', '/tmp']
    for source, dest in ((RELEASE, '/opt/hermes'), (VENV, '/opt/venv'), (PYTHON, '/opt/python'),
                         (BUNDLES / 'worker-runtime', '/opt/study'),
                         (BUNDLES / ('sg_v1' if arm == 'sg_v1' else 'sg_v2'), '/opt/sg')):
        cmd += ['--ro-bind', str(source), dest]
    taskdir = run / (f'grading-{episode}/task' if grade else 'task')
    records = run / (f'checking-{episode}/records' if check else f'grading-{episode}/records' if grade else 'records')
    grading_workspace = run / f'grading-{episode}/workspace'
    workspace = grading_workspace if grade and grading_workspace.exists() else run / 'workspace'
    cmd += ['--ro-bind', str(taskdir), '/task', '--bind', str(run / 'home'), '/home/lab',
            '--ro-bind' if (grade or check) and workspace == run / 'workspace' else '--bind', str(workspace), '/workspace', '--bind', str(records), '/records', '--clearenv']
    for name in ('inputs', 'sources', 'SPEC.md', 'DECISION_RULES.md'):
        if (workspace / name).exists():
            cmd += ['--ro-bind', str(workspace / name), '/workspace/' + name]
    env = {'HOME': '/home/lab', 'HERMES_HOME': '/home/lab/.hermes', 'PYTHONHOME': '/opt/python',
           'PATH': '/opt/python/bin:/usr/bin:/bin', 'PYTHONPATH': '/opt/study:/opt/hermes:/opt/venv/lib/python3.11/site-packages',
           'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1', 'TERMINAL_CWD': '/workspace',
           'TERMINAL_ENV': 'local', 'HERMES_YOLO': '1', 'LANG': 'C.UTF-8',
           'HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS': '90',
           'HERMES_CODEX_TTFB_TIMEOUT_SECONDS': '90', 'HERMES_STREAM_READ_TIMEOUT': '90'}
    for name, value in env.items():
        cmd += ['--setenv', name, value]
    cmd += ['--chdir', '/workspace', '/usr/bin/setpriv', f'--reuid={UID}', f'--regid={GID}',
            '--clear-groups', '--no-new-privs', '--bounding-set=-all', '--inh-caps=-all', '--ambient-caps=-all',
            '/opt/python/bin/python3.11', '/opt/study/check_worker.py' if check else '/opt/study/grade_code.py' if grade else '/opt/study/worker.py']
    path = run / (f'check-launch-{episode}.json' if check else f'grade-launch-{episode}.json' if grade else 'launch.json')
    dump(path, cmd)
    return ['/usr/bin/python3', str(ROOT / 'launch.py'), str(path)]


def user_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o755)
    os.chown(path, UID, GID)


def equivalent(actual, expected):
    # Python equality conflates True, 1 and 1.0; the task contract does not.
    return json.dumps(actual, sort_keys=True, ensure_ascii=False) == json.dumps(expected, sort_keys=True, ensure_ascii=False)


def artifact_bytes(workspace, name):
    root = workspace.resolve()
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size > 5_000_000:
        raise ValueError('invalid or oversized study artifact')
    return path.read_bytes()


def v2_prepare(run, task, control):
    sys.path.insert(0, str(BUNDLES / 'sg_v2'))
    from supergoal_runtime.v2 import Kernel
    directory = run / 'control'
    directory.mkdir(mode=0o700, exist_ok=True)
    kernel = Kernel(directory / 'kernel.sqlite')
    goal_id = control['run_id']
    try:
        state = kernel.state(goal_id)
    except KeyError:
        kernel.create(goal_id, task['contract'], max_turns=6)
        state = kernel.state(goal_id)
    if state['status'] == 'waiting':
        kernel.wake(goal_id, event_id=control.get('wake_event', ''), key=control.get('wake_key'))
    lease = kernel.claim(goal_id, f'supervisor-{os.getpid()}-{time.time_ns()}')
    if lease is None:
        raise RuntimeError('No executable work: ' + kernel.state(goal_id)['status'])
    prompt = task['prompt'] if state['turns'] == 0 else 'Continue the assigned task from the current workspace. Complete and verify remaining requirements.'
    if state['turns'] and control['arm'] != 'sg_v2_no_context':
        prompt += '\n' + kernel.context(goal_id)
    control['prompt'] = prompt
    return kernel, lease


def v2_complete(run, control, kernel, lease, state):
    if control['arm'] == 'sg_v2_no_verification':
        outcome = {'verdict': 'unverified' if state['completed'] else 'fail', 'reason': 'verification ablated'}
    else:
        user_dir(run / f'checking-{control["episode"]}/records')
        try:
            result = subprocess.run(sandbox(run, control['arm'], check=True, episode=control['episode']), capture_output=True, timeout=90)
            if result.returncode:
                outcome = {'verdict': 'unknown', 'reason': 'acceptance process failed'}
            else:
                outcome = json.loads((run / f'checking-{control["episode"]}/records/acceptance.json').read_text())
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            outcome = {'verdict': 'unknown', 'reason': type(exc).__name__}
    wait_key = control.get('wait_key') if outcome['verdict'] == 'fail' else None
    decision = kernel.finish(lease, outcome, wait_key=wait_key)
    state['status'], state['directive'] = decision['status'], decision
    dump(run / 'records/episode.json', state)
    return state


def score(run, task, arm, episode):
    workspace = run / 'workspace'
    checks = []
    if task['family'] == 'code':
        directory = run / f'grading-{episode}'
        user_dir(directory / 'records')
        (directory / 'task').mkdir()
        dump(directory / 'task/cases.json', task['private']['cases'])
        try:
            result = subprocess.run(sandbox(run, arm, grade=True, episode=episode), capture_output=True, timeout=25)
            (directory / 'stderr.log').write_bytes(result.stderr[-5000:])
            rows = json.loads((directory / 'records/grade.json').read_text())
            checks = [index < len(rows) and equivalent(rows[index].get('output'), expected) and rows[index].get('input_unchanged') is True and rows[index].get('stdlib_only') is True
                      for index, expected in enumerate(task['private']['expected'])]
        except (ValueError, OSError, subprocess.TimeoutExpired):
            checks = [False] * len(task['private']['expected'])
    else:
        name = 'result.json' if task['family'] == 'data' else 'decisions.json'
        try:
            actual = json.loads(artifact_bytes(workspace, name))
            expected = task['private']['expected']
            checks = [equivalent(actual, expected)]
            if task['family'] == 'data':
                checks.append((workspace / 'reproduce.py').is_file())
                directory = run / f'grading-{episode}'
                user_dir(directory / 'records')
                user_dir(directory / 'workspace')
                (directory / 'task').mkdir()
                dump(directory / 'task/cases.json', {'family': 'data'})
                for name, content in task['files'].items():
                    target = directory / 'workspace' / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                (directory / 'workspace/reproduce.py').write_bytes(artifact_bytes(workspace, 'reproduce.py'))
                proc = subprocess.run(sandbox(run, arm, grade=True, episode=episode), capture_output=True, timeout=25)
                regenerated = json.loads((directory / 'records/grade.json').read_text())
                checks.append(proc.returncode == 0 and regenerated.get('exit_code') == 0 and equivalent(regenerated.get('output'), expected) and regenerated.get('stdlib_only') is True)
            else:
                checks.append(len(artifact_bytes(workspace, 'BRIEF.md').decode('utf-8')) >= 100)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            checks = [False, False, False] if task['family'] == 'data' else [False, False]
    hashes = {}
    snapshot = run / f'snapshot-{episode}'
    snapshot.mkdir(exist_ok=True)
    for name in task['contract']['artifacts']:
        try:
            data = artifact_bytes(workspace, name)
            hashes[name] = hashlib.sha256(data).hexdigest()
            (snapshot / Path(name).name).write_bytes(data)
        except (OSError, ValueError):
            checks.append(False)
    return {'passed': bool(checks) and all(checks), 'checks_passed': sum(checks), 'checks_total': len(checks), 'hashes': hashes}


def run_one(task, arm, run_id):
    run = ROOT / 'runs' / run_id
    run.mkdir()  # Never replace a previous attempt.
    for name in ('home', 'workspace', 'records'):
        user_dir(run / name)
    (run / 'task').mkdir()
    for name, content in task['files'].items():
        path = run / 'workspace' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for directory, dirs, files in os.walk(run / 'workspace'):
        os.chown(directory, UID, GID)
        for name in files:
            os.chown(Path(directory) / name, UID, GID)
    public = {k: task[k] for k in ('id', 'family', 'seed', 'prompt', 'contract')}
    dump(run / 'task/task.json', public)
    (run / 'task/accept.py').write_text(task['accept'])
    dump(run / 'private-fixture.json', task['private'])
    (run / 'private-fixture.json').chmod(0o600)
    start = time.time()
    report = {'run_id': run_id, 'arm': arm, 'task_id': task['id'], 'family': task['family'],
              'seed': task['seed'], 'started': start, 'episodes': [], 'executor_calls': 0,
              'queue_seconds': 0,
              'task_sha256': hashlib.sha256(json.dumps(task, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
    provider = json.loads((ROOT / 'private/model.json').read_text())
    with ModelProxy(provider) as proxy:
        dump(run / 'task/provider.json', proxy.config())
        for episode in range(1, 7):
            remaining = 1200 - (time.time() - start)
            if report['executor_calls'] >= 48 or len(proxy.records) >= 60 or remaining < 10:
                report['status'] = 'study_budget_exhausted'
                break
            control = {'arm': arm, 'run_id': run_id, 'episode': episode,
                       'remaining_calls': 48-report['executor_calls'], 'remaining_seconds': remaining}
            try:
                with (ROOT / 'executor.lock').open('a') as lock:
                    queued_at = time.time()
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    report['queue_seconds'] += time.time() - queued_at
                    remaining = 1200 - (time.time() - start)
                    if remaining < 10:
                        report['status'] = 'study_budget_exhausted'
                        break
                    control['remaining_seconds'] = remaining
                    kernel, lease = v2_prepare(run, task, control) if arm.startswith('sg_v2') else (None, None)
                    dump(run / 'task/control.json', control)
                    proc = subprocess.run(sandbox(run, arm, episode=episode), capture_output=True, timeout=remaining)
                (run / f'worker-{episode}.log').write_bytes(proc.stdout + proc.stderr)
                if proc.returncode != 0:
                    report['status'] = 'infrastructure_error'
                    report['returncode'] = proc.returncode
                    break
                state = json.loads((run / 'records/episode.json').read_text())
                if kernel:
                    state = v2_complete(run, control, kernel, lease, state)
                report['executor_calls'] += int(state.get('api_calls') or 0)
                grade = score(run, task, arm, episode)
                report['episodes'].append({'episode': episode, 'status': state['status'], 'seconds': state['seconds'],
                                            'api_calls': state['api_calls'], 'score': grade, 'error': state['error']})
                report['status'] = state['status']
                dump(run / 'progress.json', report)
                if state['status'] != 'active':
                    break
            except subprocess.TimeoutExpired:
                report['status'] = 'wall_timeout'
                break
            except Exception as exc:
                report['status'] = 'supervisor_error'
                report['error_type'] = type(exc).__name__
                break
        report['requests'] = proxy.records
        report['rejected_requests'] = proxy.rejections
    report['seconds'] = time.time() - start
    report['transport_failures'] = sum(bool(r.get('error') or r.get('client_disconnected')) for r in report['requests'])
    report['usage_unavailable_requests'] = sum(not r.get('usage') for r in report['requests'])
    report['passed'] = bool(report['episodes']) and report['episodes'][-1]['score']['passed']
    report['declared_complete'] = report['status'] in {'completed', 'done', 'succeeded', 'unverified_done'}
    report['false_completion'] = report['declared_complete'] and not report['passed']
    report['underclaim'] = report['passed'] and not report['declared_complete']
    first = next((e['episode'] for e in report['episodes'] if e['score']['passed']), None)
    report['first_success_boundary'] = first
    report['extra_episodes_after_success'] = len(report['episodes'])-first if first else None
    dump(run / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--tag', required=True)
    parser.add_argument('--seeds', default='1101')
    parser.add_argument('--families', default=','.join(FAMILIES))
    parser.add_argument('--arms', default=','.join(ARMS))
    args = parser.parse_args()
    plan = []
    rng = random.Random(41005)
    for seed in map(int, args.seeds.split(',')):
        for family in args.families.split(','):
            block = [(family, seed, arm) for arm in args.arms.split(',')]
            rng.shuffle(block)
            plan.extend(block)
    plan_path = ROOT / 'receipts' / (args.tag + '-plan.json')
    if plan_path.exists():
        raise RuntimeError('study tag already exists; previous attempts must be retained')
    dump(plan_path, plan)
    reports = []
    for family, seed, arm in plan:
        report = run_one(build(family, seed), arm, f'{args.tag}-{family}-{seed}-{arm}')
        reports.append(report)
        dump(ROOT / 'receipts' / (args.tag + '-results.json'), reports)
        print(json.dumps({k: report[k] for k in ('run_id', 'status', 'passed', 'false_completion', 'seconds')}), flush=True)


if __name__ == '__main__':
    main()
