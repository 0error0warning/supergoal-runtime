"""Finite, two-hour staged-input experiment with a real supervisor restart.

Run as a transient systemd service with Restart=on-failure. Exit 75 is injected
ONCE after a committed waiting boundary. A new process reloads the experiment
checkpoint and the worker reopens the same kernel database.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import time

from model_proxy import ModelProxy
from supervisor import ROOT, UID, GID, dump, sandbox, user_dir, v2_prepare, v2_complete

RUN_ID = os.environ.get('SUPERGOAL_SOAK_ID', 'soak-v2-r2-20261005')
RUN = ROOT / 'runs' / RUN_ID


def task():
    prompt = ('Maintain a cumulative sensor summary over inputs/batch-*.json. Each batch is a JSON list of '
              'records with id and integer value. Deduplicate by id: later numbered batch wins, and later '
              'row wins inside a batch. Write summary.json containing exactly count, sum, ids (sorted). '
              'Also write update.py that reproducibly regenerates summary.json from available batches. '
              'Inputs arrive in three phases over about two hours. Work on currently available files. '
              'The job is complete only when inputs/CLOSED exists and all three batches are represented. '
              'The inputs directory is read-only; only the host delivers input files and the CLOSED marker. '
              'If future batches are unavailable, finish the current turn stating that you are waiting; '
              'the runtime will park and wake you on input arrival. Do not sleep or poll for future files. '
              'On wakeup, inspect new files and update the summary. Public acceptance: python /task/accept.py.')
    return {'id': RUN_ID, 'family': 'staged_data', 'seed': 1101, 'prompt': prompt,
            'contract': {'outcome': prompt, 'artifacts': ['summary.json', 'update.py'],
                         'checks': [{'id': 'closed-input-and-summary', 'argv': ['/opt/python/bin/python3.11', '/task/accept.py']}]}}


ACCEPT = """import json
from pathlib import Path
assert Path('inputs/CLOSED').exists(), 'Waiting for the final input delivery'
assert len(list(Path('inputs').glob('batch-*.json')))==3
x=json.loads(Path('summary.json').read_text())
assert set(x)=={'count','sum','ids'}
assert x['count']==len(x['ids']) and x['ids']==sorted(set(x['ids']))
assert Path('update.py').is_file()
print('input closed and public summary shape passed')
"""

BATCHES = [[{'id': 'alpha', 'value': 7}, {'id': 'beta', 'value': 0}],
           [{'id': 'gamma', 'value': -4}, {'id': 'alpha', 'value': 11}],
           [{'id': 'beta', 'value': 5}, {'id': 'delta', 'value': 8}, {'id': 'delta', 'value': 9}]]


def save(checkpoint):
    temp = RUN / 'checkpoint.tmp'
    dump(temp, checkpoint)
    temp.replace(RUN / 'checkpoint.json')


def main():
    checkpoint_path = RUN / 'checkpoint.json'
    if not checkpoint_path.exists():
        RUN.mkdir()
        for name in ('home', 'workspace', 'records'):
            user_dir(RUN / name)
        user_dir(RUN / 'workspace/inputs')
        (RUN / 'task').mkdir()
        dump(RUN / 'task/task.json', task())
        (RUN / 'task/accept.py').write_text(ACCEPT)
        checkpoint = {'run_id': RUN_ID, 'started': time.time(), 'phase': 0,
                      'requests': [], 'episodes': [], 'executor_calls': 0,
                      'supervisor_instances': [], 'restart_injected': False}
    else:
        checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint['supervisor_instances'].append({'pid': os.getpid(), 'started': time.time()})
    save(checkpoint)
    if checkpoint['phase'] >= 3:
        return
    for phase in range(checkpoint['phase'], 3):
        due = checkpoint['started'] + [0, 3600, 7205][phase]
        while time.time() < due:
            time.sleep(min(30, due-time.time()))
        batch = RUN / f'workspace/inputs/batch-{phase}.json'
        dump(batch, BATCHES[phase])
        os.chown(batch, UID, GID)
        if phase == 2:
            (RUN / 'workspace/inputs/CLOSED').write_text('All scheduled inputs delivered.\n')
        provider = json.loads((ROOT / 'private/model.json').read_text())
        with ModelProxy(provider, limit=60-len(checkpoint['requests'])) as proxy:
            dump(RUN / 'task/provider.json', proxy.config())
            control = {'arm': 'sg_v2', 'run_id': RUN_ID, 'episode': phase+1,
                       'remaining_calls': 48-checkpoint['executor_calls'], 'remaining_seconds': 900,
                       'wait_key': 'next-input' if phase < 2 else None,
                       'wake_key': 'next-input', 'wake_event': f'delivery-{phase}'}
            with (ROOT / 'executor.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                kernel, lease = v2_prepare(RUN, task(), control)
                dump(RUN / 'task/control.json', control)
                result = subprocess.run(sandbox(RUN, 'sg_v2', episode=phase+1), capture_output=True, timeout=1000)
            (RUN / f'phase-{phase}.log').write_bytes(result.stdout+result.stderr)
            checkpoint['requests'].extend(proxy.records)
        if result.returncode:
            checkpoint['status'] = 'infrastructure_error'
            save(checkpoint)
            # Keep the failure evidence; do not automatically repeat an uncertain execution.
            return
        episode = json.loads((RUN / 'records/episode.json').read_text())
        episode = v2_complete(RUN, control, kernel, lease, episode)
        snapshot = json.loads((RUN / 'workspace/summary.json').read_text())
        latest = {}
        for items in BATCHES[:phase+1]:
            for item in items:
                latest[item['id']] = item['value']
        expected = {'count': len(latest), 'sum': sum(latest.values()), 'ids': sorted(latest)}
        checkpoint['episodes'].append({'phase': phase, 'boundary_time': time.time(),
            'status': episode['status'], 'worker_instance': episode['process_instance'],
            'worker_pid_in_namespace': episode['pid'], 'seconds': episode['seconds'],
            'api_calls': episode['api_calls'], 'independent_pass': snapshot == expected})
        checkpoint['executor_calls'] += int(episode['api_calls'] or 0)
        checkpoint['phase'] = phase+1
        checkpoint['status'] = episode['status']
        save(checkpoint)
        print(json.dumps(checkpoint['episodes'][-1]), flush=True)
        if phase == 0 and not checkpoint['restart_injected']:
            checkpoint['restart_injected'] = True
            save(checkpoint)
            raise SystemExit(75)
    checkpoint['elapsed_seconds'] = time.time()-checkpoint['started']
    checkpoint['active_episode_seconds'] = sum(x['seconds'] for x in checkpoint['episodes'])
    checkpoint['non_episode_seconds'] = checkpoint['elapsed_seconds']-checkpoint['active_episode_seconds']
    checkpoint['passed'] = (checkpoint['status'] == 'succeeded'
                            and all(x['independent_pass'] for x in checkpoint['episodes'])
                            and checkpoint['elapsed_seconds'] >= 7200
                            and len(checkpoint['supervisor_instances']) >= 2)
    save(checkpoint)
    dump(ROOT / 'receipts' / (RUN_ID + '-results.json'), checkpoint)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Only the intentional SystemExit(75) is automatically restarted. An
        # unknown in-flight failure is evidence requiring reconciliation.
        path = RUN / 'checkpoint.json'
        checkpoint = json.loads(path.read_text()) if path.exists() else {}
        checkpoint.update(status='supervisor_error', error_type=type(exc).__name__)
        save(checkpoint)
        print(json.dumps({'status': 'supervisor_error', 'error_type': type(exc).__name__}), flush=True)
