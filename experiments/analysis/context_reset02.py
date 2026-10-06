"""Corrected context-policy study; preserves the frozen r3 executor.

Version 02 removes an invalid value anchor from public acceptance and uses new
fixture seeds. Version 01 and all its failures remain unchanged.

Server-only. Source is never mounted inside the executor. All arms share v2's
control/acceptance, receive the same three staged deliveries, and lose previous
conversation records between episodes. Workspace and public task file remain
available, including for the minimal-context control.
"""
from __future__ import annotations

import csv
import fcntl
import hashlib
import inspect
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

STUDY = Path(os.environ['SUPERGOAL_STUDY_BUNDLES'])/'study'
sys.path.insert(0, str(STUDY))
from model_proxy import ModelProxy
from supervisor import (
    GID,
    ROOT,
    UID,
    dump,
    sandbox,
    score,
    user_dir,
    v2_complete,
    v2_prepare,
)
from tasks import build, reference

TAG = 'context-reset02'
POLICIES = ('minimal_continue', 'original_goal', 'durable_state')
SEEDS = (8701, 8702)


def fixture(seed):
    task = build('data', seed)
    prompt = ('Maintain result.json and reproduce.py as three CSV shards arrive one at a time. '
              'Use SPEC.md for event ordering and selection. At EVERY delivery, produce the correct '
              'result for ALL CURRENTLY AVAILABLE shards in filename order, preserving row order. '
              'The script must work when only one or two shards exist as well as when all three exist. '
              'The file list in SPEC.md describes the eventual full dataset. Missing future shards '
              'are expected; process the available shards now. The final goal completes only after '
              'inputs/CLOSED exists and all three shards are represented. Until then, finish the '
              'current turn saying you are waiting. The runtime delivers inputs and wakes you; do '
              'not sleep or poll. Each wake starts with no previous conversation history; the '
              'workspace and /task/task.json remain available. Check actual outputs before ending. '
              'Only the host can write inputs. Public check: python /task/accept.py.')
    task['id'] = f'context-data-{seed}'
    task['prompt'] = prompt
    task['contract']['outcome'] = prompt
    # The zero-valued anchor was not invariant under valid later updates.
    task['accept'] = task['accept'].replace("assert {'id':'zero','value':0} in x\n", '')
    task['accept'] = "from pathlib import Path\nassert Path('inputs/CLOSED').exists(), 'Awaiting final shard'\n"+task['accept']
    return task


def for_phase(task, phase):
    result = json.loads(json.dumps(task))
    names = {f'inputs/part-{i}.csv' for i in range(phase+1)}
    result['files'] = {k:v for k,v in task['files'].items() if not k.startswith('inputs/') or k in names}
    if phase == 2:
        result['files']['inputs/CLOSED'] = 'All shards delivered.\n'
    events = []
    for name in sorted(names):
        events.extend(csv.DictReader(io.StringIO(task['files'][name])))
    result['private']['expected'] = reference(events, '2026-06-01T12:00:00Z')
    return result


def run(seed, policy):
    task = fixture(seed)
    run_id = f'{TAG}-{seed}-{policy}'
    directory = ROOT/'runs'/run_id
    directory.mkdir()
    for name in ('home', 'workspace', 'records', 'workspace/inputs'):
        user_dir(directory/name)
    (directory/'task').mkdir()
    (directory/'archive').mkdir(mode=0o700)
    (directory/'workspace/SPEC.md').write_text(task['files']['SPEC.md'])
    os.chown(directory/'workspace/SPEC.md', UID, GID)
    public = {k: task[k] for k in ('id', 'family', 'seed', 'prompt', 'contract')}
    dump(directory/'task/task.json', public)
    (directory/'task/accept.py').write_text(task['accept'])
    report = {'run_id': run_id, 'seed': seed, 'policy': policy, 'started': time.time(),
              'task_sha256': hashlib.sha256(json.dumps(task, sort_keys=True).encode()).hexdigest(),
              'episodes': [], 'executor_calls': 0, 'queue_seconds': 0, 'status': 'initializing'}
    arm = 'sg_v2' if policy == 'durable_state' else 'sg_v2_no_context'
    provider = json.loads((ROOT/'private/model.json').read_text())
    with ModelProxy(provider) as proxy:
        dump(directory/'task/provider.json', proxy.config())
        for episode in range(1, 7):
            phase = min(episode-1, 2)
            if episode <= 3:
                name = f'inputs/part-{phase}.csv'
                target = directory/'workspace'/name
                target.write_text(task['files'][name])
                os.chown(target, UID, GID)
                if phase == 2:
                    (directory/'workspace/inputs/CLOSED').write_text('All shards delivered.\n')
            # Preserve records outside the agent's namespace before starting fresh.
            archive = directory/f'archive/before-episode-{episode}'
            archive.mkdir(mode=0o700)
            for path in (directory/'records').iterdir():
                path.rename(archive/path.name)
            # Hermes also persists transcripts in its home. Archive the entire
            # isolated home so it cannot silently restore the previous session.
            (directory/'home').rename(archive/'hermes-home')
            user_dir(directory/'home')
            remaining = 1200-(time.time()-report['started'])
            if remaining < 10 or report['executor_calls'] >= 48 or len(proxy.records) >= 60:
                report['status'] = 'study_budget_exhausted'
                break
            control = {'arm': arm, 'run_id': run_id, 'episode': episode,
                       'remaining_calls': 48-report['executor_calls'], 'remaining_seconds': remaining,
                       'wait_key': 'next-input' if phase < 2 else None,
                       'wake_key': 'next-input', 'wake_event': f'delivery-{phase}'}
            try:
                with (ROOT/'executor.lock').open('a') as lock:
                    queued = time.time()
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    report['queue_seconds'] += time.time()-queued
                    remaining = 1200-(time.time()-report['started'])
                    if remaining < 10:
                        report['status'] = 'study_budget_exhausted'
                        break
                    control['remaining_seconds'] = remaining
                    kernel, lease = v2_prepare(directory, task, control)
                    if policy == 'original_goal':
                        control['prompt'] = task['prompt']
                    dump(directory/'task/control.json', control)
                    proc = subprocess.run(sandbox(directory, arm, episode=episode), capture_output=True,
                                          timeout=remaining, check=False)
                (directory/f'worker-{episode}.log').write_bytes(proc.stdout+proc.stderr)
                if proc.returncode:
                    report['status'] = 'infrastructure_error'
                    report['returncode'] = proc.returncode
                    break
                state = json.loads((directory/'records/episode.json').read_text())
                state = v2_complete(directory, control, kernel, lease, state)
                grade = score(directory, for_phase(task, phase), arm, episode)
                report['executor_calls'] += int(state['api_calls'] or 0)
                report['episodes'].append({'episode': episode, 'delivery_phase': phase,
                    'history_reset': True, 'fresh_hermes_home': True,
                    'status': state['status'], 'score': grade,
                    'api_calls': state['api_calls'], 'seconds': state['seconds'],
                    'prompt_bytes': len(control['prompt'].encode()),
                    'prompt_sha256': hashlib.sha256(control['prompt'].encode()).hexdigest(),
                    'worker_instance': state['process_instance'], 'error': state['error']})
                report['status'] = state['status']
                dump(directory/'progress.json', report)
                if phase < 2 and state['status'] != 'waiting':
                    break
                if phase == 2 and state['status'] != 'active':
                    break
            except subprocess.TimeoutExpired:
                report['status'] = 'wall_timeout'
                break
            except Exception as exc:  # noqa: BLE001 - Preserve every failed study attempt.
                report['status'] = 'supervisor_error'
                report['error_type'] = type(exc).__name__
                break
        report['requests'] = proxy.records
        report['rejected_requests'] = proxy.rejections
    report['seconds'] = time.time()-report['started']
    report['passed'] = (report['status'] == 'succeeded' and len(report['episodes']) >= 3
                        and all(x['score']['passed'] for x in report['episodes']))
    report['final_artifact_pass'] = bool(report['episodes']) and report['episodes'][-1]['score']['passed']
    dump(directory/'report.json', report)
    return report


def contract_preflight():
    path = ROOT/f'receipts/{TAG}-preflight.json'
    if path.exists():
        raise RuntimeError('Retain the earlier preflight attempt')
    rows = []
    with tempfile.TemporaryDirectory(prefix='supergoal-contract-preflight-') as temporary:
        for seed in (8602, *SEEDS):
            task = fixture(seed)
            directory = Path(temporary)/str(seed)
            directory.mkdir()
            for name, content in task['files'].items():
                target = directory/name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            (directory/'inputs/CLOSED').write_text('closed')
            reference_script = ('import csv,json\nfrom datetime import datetime\nfrom pathlib import Path\n'+
                                inspect.getsource(reference)+
                                "\nevents=[]\nfor path in sorted(Path('inputs').glob('part-*.csv')):\n"
                                "    events.extend(csv.DictReader(path.open()))\n"
                                "Path('result.json').write_text(json.dumps(reference(events,'2026-06-01T12:00:00Z')))\n")
            (directory/'reproduce.py').write_text(reference_script)
            subprocess.run([sys.executable, 'reproduce.py'], cwd=directory, check=True, timeout=20)
            output = json.loads((directory/'result.json').read_text())
            old = subprocess.run([sys.executable, '-c', build('data', seed)['accept']],
                                 cwd=directory, capture_output=True, check=False, timeout=20)
            fixed = subprocess.run([sys.executable, '-c', task['accept']],
                                   cwd=directory, capture_output=True, check=False, timeout=20)
            rows.append({'seed': seed, 'reference_matches_expected': output == task['private']['expected'],
                         'old_checker_exit_code': old.returncode,
                         'corrected_checker_exit_code': fixed.returncode,
                         'corrected_checker_sha256': hashlib.sha256(task['accept'].encode()).hexdigest(),
                         'reference_output_sha256': hashlib.sha256((directory/'result.json').read_bytes()).hexdigest()})
    value = {'kind': 'known_good_fixture_acceptance_preflight', 'performed_at': time.time(),
             'live_model_calls': 0, 'rows': rows,
             'passed': all(r['reference_matches_expected'] and r['corrected_checker_exit_code'] == 0 for r in rows),
             'limit': 'Satisfiability on these known-correct fixtures, not complete verifier coverage.'}
    dump(path, value)
    if not value['passed']:
        raise RuntimeError('Correct public checks before making any model call')


def main():
    receipt = ROOT/f'receipts/{TAG}-results.json'
    if receipt.exists():
        raise RuntimeError('Do not overwrite or selectively rerun an existing experiment')
    contract_preflight()
    plan = []
    rng = random.Random(50105)
    for seed in SEEDS:
        block = [(seed, policy) for policy in POLICIES]
        rng.shuffle(block)
        plan.extend(block)
    registration = {'kind': 'supplementary_exploratory_context_policy_study', 'registered': time.time(),
                    'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    'candidate': 'candidate-r3', 'plan': plan,
                    'fixture_sha256': {str(s): hashlib.sha256(json.dumps(fixture(s), sort_keys=True).encode()).hexdigest() for s in SEEDS},
                    'limits': {'outer_episodes': 6, 'executor_calls': 48, 'physical_requests': 60, 'wall_seconds': 1200},
                    'scope': 'Three context policies; same control and verification, two related staged-data fixtures. No previous conversation history. Workspace and public goal file remain accessible to every arm. No duration or generality claim.'}
    registration_path = ROOT/f'receipts/{TAG}-registration.json'
    if registration_path.exists():
        raise RuntimeError('Registration already exists; retain previous attempt')
    dump(registration_path, registration)
    results = []
    for seed, policy in plan:
        report = run(seed, policy)
        results.append(report)
        dump(receipt, results)
        print(json.dumps({k: report[k] for k in ('run_id', 'status', 'passed', 'seconds')}), flush=True)


if __name__ == '__main__':
    main()
