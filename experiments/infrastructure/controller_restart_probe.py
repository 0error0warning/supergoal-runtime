"""Finite live controller-loss probe, excluded from capability benchmarks.

An external supervisor kills the controller's entire systemd cgroup. It fences
the separate Docker task, archives its filesystem, and starts a fresh controller
against the same Kernel and request journal. This prototype tests process loss,
not VM/power loss, production gateway recovery, or remote side-effect replay.
"""
import asyncio
from dataclasses import asdict
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
import uuid

from artifact_store import DockerCheckpointStore
from harbor_agent import HermesSupergoalStudy, ROOT
from model_proxy import ModelProxy
from snapshot_lifecycle import capture_snapshot
from supergoal_runtime.v2 import Kernel, Lease, StaleLease


LABEL = 'controller-restart02'
CANDIDATE = ROOT / 'candidates/scb-transfer01'
DOCKER = ['/usr/bin/docker', '-H', 'unix://' + str(ROOT / 'run/docker.sock')]
IMAGE = 'sha256:f0d6e67fe473a244c4e7c3d980df8651e5e05f823a3a4b662490a5bb2188c9c2'
PROMPT = '''Transform /app/input.csv into /app/normalized.jsonl, preserving row order.
For each input row output exactly an object with integer fields id and squared,
where squared is the square of the input quantity. Validate your intermediate
file, then write exactly ready followed by a newline to /app/prepared.txt.
For this process-recovery integration test, if prepared.txt did NOT already exist
when this session began, execute sleep 30 after writing it and before creating
the final summary. If this session began with prepared.txt already present,
reinspect the files and skip this one-time delay.
Finally write /app/summary.json with count (number of input rows) and sum_squared
(sum of squared quantities). Verify all outputs against the input and finish.
'''
CHECK = '''import csv,json,pathlib,hashlib
p=pathlib.Path('/app');rows=list(csv.DictReader((p/'input.csv').open()))
expected=[{'id':int(r['id']),'squared':int(r['quantity'])**2} for r in rows]
actual=[json.loads(s) for s in (p/'normalized.jsonl').read_text().splitlines()]
assert actual==expected
assert json.loads((p/'summary.json').read_text())=={'count':len(expected),'sum_squared':sum(r['squared'] for r in expected)}
print(json.dumps({'count':len(expected),'artifacts_sha256':{n:hashlib.sha256((p/n).read_bytes()).hexdigest() for n in ['normalized.jsonl','summary.json']}}))
'''


def save(path, value):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as handle:
        json.dump(value, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def command(argv, *, timeout=30, check=True):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError(f'{argv[0]} failed: ' + result.stderr[-1200:])
    return result


async def docker(*args, timeout=45):
    return (await asyncio.to_thread(command, DOCKER + list(map(str, args)), timeout=timeout)).stdout.strip()


class Environment:
    def __init__(self, container):
        self.container = container

    async def exec(self, text, timeout_sec=20, user='root'):
        result = await asyncio.to_thread(command, DOCKER + ['exec', '--user', user, self.container,
                                         'bash', '-c', text], timeout=timeout_sec, check=False)
        return SimpleNamespace(return_code=result.returncode, stdout=result.stdout, stderr=result.stderr)


async def controller(config_path):
    config = json.loads(Path(config_path).read_text())
    control = ROOT / 'control' / config['control_id']
    phase = config['phase']
    seconds = config['deadline_epoch'] - time.time()
    if seconds < 5:
        raise TimeoutError('Overall probe deadline reached; restart grants no extra time')
    logs = control / f'phase-{phase}-logs'
    logs.mkdir(exist_ok=False)
    agent = HermesSupergoalStudy(logs_dir=logs, arm='sg_v2', max_requests=config['max_requests'],
                                max_episodes=3, budget_seconds=seconds)
    # The base allocates an unused empty directory. It has no model/DB state;
    # only this newly created empty directory is removed, never existing work.
    agent.control.rmdir()
    agent.uid, agent.control = config['control_id'], control
    agent.deadline = time.monotonic() + seconds
    if phase == 2:
        agent.rounds = [{'role': 'executor', 'exit_code': -signal.SIGKILL,
                         'model_calls': config['requests_before_restart'], 'interrupted': True}]
    await agent.setup(Environment(config['container_id']))
    kernel = Kernel(control / 'kernel.sqlite3')
    if phase == 1:
        kernel.create(agent.uid, {'outcome': PROMPT}, max_turns=3)
    lease = kernel.claim(agent.uid, f'controller-phase-{phase}', ttl=120)
    if lease is None:
        raise RuntimeError('Restart controller has no work lease')
    save(control / f'phase-{phase}-lease.json', asdict(lease))
    agent.active_lease = lease
    heartbeat = asyncio.create_task(agent.lease_heartbeat(kernel))
    provider = json.loads((ROOT / 'private/model.json').read_text())
    try:
        with ModelProxy(provider, limit=config['max_requests'], journal=control / 'request-journal.json') as proxy:
            save(control / f'phase-{phase}-started.json', {'pid': os.getpid(), 'requests_already_charged': len(proxy.records)})
            result = await agent.episode(kernel.context(agent.uid), proxy, role='executor', container=agent.container)
            check = await asyncio.to_thread(command, DOCKER + ['exec', agent.container, 'python3', '-c', CHECK], check=False)
            verdict = 'pass' if check.returncode == 0 and result.get('completed') is True else 'unknown'
            evidence = {'verdict': verdict, 'reason': 'Trusted fixture checker; recovery integration scope only',
                        'checker_exit': check.returncode, 'observations': check.stdout[-3000:]}
            state = kernel.finish(lease, evidence)
            save(control / f'phase-{phase}-finished.json', {'state': state, 'acceptance': evidence,
                 'used_requests': len(proxy.records), 'sdk_completed': result.get('completed'), 'rounds': agent.rounds})
    finally:
        agent.active_lease = None
        heartbeat.cancel()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass


def start_controller(config, phase, folder):
    path = folder / f'phase-{phase}-config.json'
    save(path, {**config, 'phase': phase})
    unit = f'supergoal-{LABEL}-phase{phase}'
    pythonpath = ':'.join(map(str, [Path(__file__).parent, CANDIDATE, CANDIDATE / 'experiments',
                                   CANDIDATE / 'experiments/public_benchmarks']))
    command(['systemd-run', '--unit=' + unit, '--property=Type=exec', '--property=KillMode=control-group',
        '--property=Restart=no', '--property=RuntimeMaxSec=1200', '--property=MemoryMax=3G',
        '--property=CPUQuota=100%', '--setenv=PYTHONPATH=' + pythonpath,
        '--setenv=PYTHONDONTWRITEBYTECODE=1', str(ROOT / 'harbor-venv/bin/python'),
        str(Path(__file__).resolve()), '--controller', str(path)])
    return unit


def unit_state(unit):
    return command(['systemctl', 'show', unit, '--property=ActiveState', '--value']).stdout.strip()


def supervisor():
    target = ROOT / 'setup' / (LABEL + '.json')
    if target.exists():
        raise FileExistsError('Do not retry an already started recovery probe')
    regpath = ROOT / 'setup' / ('registration-' + LABEL + '.json')
    registration = json.loads(regpath.read_text())
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != registration['operator_sha256']:
        raise ValueError('Registered recovery operator changed')
    if hashlib.sha256(Path(__file__).with_name('artifact_store.py').read_bytes()).hexdigest() != registration['archive_operator_sha256']:
        raise ValueError('Registered archive operator changed')
    for name, expected in registration['candidate_source_sha256'].items():
        if hashlib.sha256((CANDIDATE / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Registered candidate changed: ' + name)
    receipt = {'status': 'waiting_reserved_capacity', 'fault_activated': False,
        'model': 'devin/swe-2', 'max_requests': 32,
        'scope': 'Synthetic process/controller-loss integration, not benchmark capability or production recovery',
        'automatic_trial_retries': 0, 'source_sha256': registration['operator_sha256'],
        'registration_sha256': hashlib.sha256(regpath.read_bytes()).hexdigest()}
    save(target, receipt)
    # This replaces one of the two slots used by mechanism01, independently of
    # the unchanged six-CPU public/SCBench pool. Never overlap that lane.
    admission_end = datetime.datetime.fromisoformat('2026-10-06T14:14:06+00:00').timestamp()
    while True:
        old = json.loads((ROOT / 'setup/mechanism01-receipt.json').read_text())
        if old['status'].startswith('complete') and all(r['status'] != 'running' for r in old['rows']):
            break
        if time.time() + 1200 > admission_end:
            receipt['status'] = 'not_started_admission_closed'
            save(target, receipt)
            return
        time.sleep(10)
    uid = uuid.uuid4().hex
    folder = ROOT / 'control' / uid
    folder.mkdir(mode=0o700)
    receipt.update(status='running', control_id=uid, started_epoch=time.time())
    save(target, receipt)
    config = {'control_id': uid, 'max_requests': 32, 'deadline_epoch': time.time() + 900}
    containers, units = [], []
    try:
        def create(image, suffix):
            return command(DOCKER + ['run', '-d', '--network', 'none', '--cpus', '1', '--memory', '2g',
                '--memory-swap', '2g', '--label', 'supergoal.probe=' + LABEL, '--name', LABEL + '-' + suffix,
                '--workdir', '/app', '--entrypoint', '/bin/sh', image, '-c', 'sleep infinity']).stdout.strip()
        original = create(IMAGE, 'original')
        containers.append(original)
        config['container_id'] = original
        source = folder / 'input.csv'
        source.write_text('id,quantity\n' + ''.join(f'{i},{i}\n' for i in range(1, 201)))
        command(DOCKER + ['cp', str(source), original + ':/app/input.csv'])
        first = start_controller(config, 1, folder)
        units.append(first)
        while time.time() < config['deadline_epoch'] - 300:
            marker = command(DOCKER + ['exec', original, 'sh', '-c',
                'test -f /app/prepared.txt && test ! -e /app/summary.json'], check=False)
            if marker.returncode == 0 and (folder / 'request-journal.json').exists():
                break
            if unit_state(first) not in {'active', 'activating'}:
                raise RuntimeError('Initial controller ended before the registered fault point')
            time.sleep(1)
        else:
            raise TimeoutError('Fault point not reached; no restart success can be claimed')
        if not json.loads((folder / 'request-journal.json').read_text())['records']:
            raise RuntimeError('A real model request must precede the fault')
        command(['systemctl', 'kill', '--kill-whom=all', '--signal=KILL', first])
        command(['systemctl', 'stop', first])
        if unit_state(first) not in {'inactive', 'failed'}:
            raise RuntimeError('Old controller cgroup has not stopped')
        # Take the authoritative prefix only after every old proxy thread is
        # dead; an upstream response may finish between fault detection and kill.
        before = json.loads((folder / 'request-journal.json').read_text())['records']
        command(DOCKER + ['stop', '--time', '2', original])
        info = json.loads(command(DOCKER + ['inspect', original]).stdout)[0]
        if info['State']['Running'] or info['HostConfig']['NetworkMode'] != 'none' or info['Mounts']:
            raise RuntimeError('Local-only task fencing was not established')
        receipt.update(fault_activated=True, killed_unit=first, prior_requests=len(before), old_task_stopped=True)
        save(target, receipt)
        snap = {}
        image = asyncio.run(capture_snapshot(docker, original, 'sg-restart:' + uid, snap))
        store = DockerCheckpointStore(ROOT / 'checkpoint-store', DOCKER)
        manifest = store.save(image)
        store.verify(manifest)
        evidence = {'execution_stopped': True, 'external_effects': 'sandbox_local_only', 'snapshot_id': image,
                    'observed_by': 'external-supervisor-systemd-and-docker-inspect',
                    'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest()}
        kernel = Kernel(folder / 'kernel.sqlite3')
        lease = Lease(**json.loads((folder / 'phase-1-lease.json').read_text()))
        kernel.interrupt(lease, reason='Registered full controller-cgroup SIGKILL')
        try:
            kernel.finish(lease, {'verdict': 'pass'})
        except StaleLease:
            receipt['stale_owner_rejected'] = True
        else:
            raise RuntimeError('Old owner was incorrectly allowed to finish')
        recovered = kernel.recover_execution(lease, evidence=evidence)
        if kernel.recover_execution(lease, evidence=evidence) != recovered or recovered['turns'] != 1:
            raise RuntimeError('Recovery acknowledgement was not idempotent')
        receipt.update(recovery_idempotent=True, snapshot=snap, manifest=str(manifest), recovery=recovered)
        save(target, receipt)
        replacement = create(image, 'replacement')
        containers.append(replacement)
        config.update(container_id=replacement, requests_before_restart=len(before))
        second = start_controller(config, 2, folder)
        units.append(second)
        while unit_state(second) in {'active', 'activating'}:
            if time.time() > config['deadline_epoch'] + 60:
                raise TimeoutError('Overall restart probe expired')
            time.sleep(2)
        finished = json.loads((folder / 'phase-2-finished.json').read_text())
        after = json.loads((folder / 'request-journal.json').read_text())['records']
        receipt.update(final_state=finished['state'], acceptance=finished['acceptance'], used_requests=len(after),
                       old_reservations_preserved=after[:len(before)] == before,
                       request_limit_preserved=len(before) < len(after) <= config['max_requests'],
                       new_controller_loaded_prior_budget=json.loads((folder / 'phase-2-started.json').read_text())['requests_already_charged'] == len(before))
        receipt['status'] = 'passed' if finished['state']['status'] == 'succeeded' and all(
            receipt[k] for k in ['stale_owner_rejected', 'old_reservations_preserved',
                                'request_limit_preserved', 'new_controller_loaded_prior_budget']) else 'failed'
    except BaseException as exc:
        receipt.update(status='failed', error_type=type(exc).__name__, error=str(exc)[:1800])
        raise
    finally:
        cleanup_errors = []
        for unit in units:
            try:
                result = command(['systemctl', 'stop', unit], check=False)
                # Successful transient units can already have been garbage
                # collected. Verify absence/inactivity instead of interpreting
                # systemctl's 'not loaded' response as a leaked running process.
                if result.returncode and unit_state(unit) not in {'inactive', 'failed'}:
                    cleanup_errors.append({'unit': unit, 'error': result.stderr[-500:]})
            except Exception as exc:
                cleanup_errors.append({'unit': unit, 'error': str(exc)[:500]})
        for container in containers:
            try:
                result = command(DOCKER + ['rm', '-f', container], check=False)
                if result.returncode:
                    cleanup_errors.append({'container': container, 'error': result.stderr[-500:]})
            except Exception as exc:
                cleanup_errors.append({'container': container, 'error': str(exc)[:500]})
        receipt['cleanup_errors'] = cleanup_errors
        if cleanup_errors and receipt['status'] == 'passed':
            receipt['status'] = 'failed_cleanup'
        receipt['finished_epoch'] = time.time()
        save(target, receipt)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--controller':
        asyncio.run(controller(sys.argv[2]))
    else:
        supervisor()
