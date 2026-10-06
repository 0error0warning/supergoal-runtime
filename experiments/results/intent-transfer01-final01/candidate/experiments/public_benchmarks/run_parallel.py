"""Run each frozen row once, sharing a bounded resource pool with other cohorts."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from resource_pool import ResourcePool, reservation
from run_registered import available_memory, check_environment, check_sources, now, summarize, write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root, label = args.root, args.label
    registration_path = args.candidate / "experiments/public_benchmarks" / f"registration-{label}.json"
    registration = json.loads(registration_path.read_text())
    upstream = root / "upstream/terminal-bench-2-1"
    environment_path = root / "setup/environment-parallel24.json"
    environment_lock = json.loads(environment_path.read_text())
    check_sources(args.candidate, registration, upstream)
    check_environment(root, upstream, environment_lock)
    tasks = {t["task_id"]: t for t in registration["tasks"]}
    development = json.loads((root / 'setup/worker-fault-dev05.json').read_text())
    required = [(None, 'sg_v2'), ('worker_kill', 'sg_v2'), ('worker_kill', 'repeat_goal'),
                ('stream_disconnect', 'native'), ('stream_disconnect', 'sg_v2')]
    for fault, arm in required:
        matches = [r for r in development['rows'] if r['fault'] == fault and r['arm'] == arm]
        if len(matches) != 1 or not matches[0].get('artifact_passed') or (fault and not matches[0].get('fault_triggered')):
            raise ValueError('Development gate has not passed')
    receipt_path = root / "setup" / f"{label}-rows-000-{len(registration['planned_order']):03d}.json"
    if receipt_path.exists():
        raise FileExistsError("Started cohort must be audited, never automatically rerun")
    receipt = {"status": "waiting_for_oracles", "started_at": now(), "rows": [],
               "automatic_trial_retries": 0, "registration_sha256": hashlib.sha256(registration_path.read_bytes()).hexdigest(),
               "operator_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
               "shared_pool": {"cpus": 6, "memory_mb": 49152, "max_workers": 6},
               "environment_lock_sha256": hashlib.sha256(environment_path.read_bytes()).hexdigest()}
    write_json(receipt_path, receipt)
    deadline = datetime.datetime.fromisoformat(registration["stop_admission_at_utc"]).timestamp()
    oracle_path = root / "setup/tb-oracle-parallel24-receipt.json"
    pool = ResourcePool(root / "run/parallel-resource-pool.json")
    oracle = json.loads(oracle_path.read_text())
    reference_reservation = None
    if not oracle['status'].startswith('complete'):
        reference_reservation = label + '-reference-reserve'
        if not pool.acquire(reference_reservation, {'cpus': 4, 'memory_mb': 16384}):
            raise RuntimeError('Cannot reserve the ongoing reference workload before model admission')
    receipt['dispatch_policy'] = 'Per-task reference gate; reserve all four oracle CPUs until preflight finishes'
    env = dict(os.environ, DOCKER_HOST="unix://" + str(root / "run/docker.sock"),
               PYTHONPATH=os.pathsep.join(map(str, [args.candidate, args.candidate / "experiments",
                                                  args.candidate / "experiments/public_benchmarks"])))
    pending, running = [], {}
    for index, row in enumerate(registration["planned_order"]):
        entry = {"index": index, **row, "status": "waiting_reference"}
        receipt["rows"].append(entry)
        pending.append(entry)
    receipt["status"] = "running"
    stopped = False
    while pending or running:
        oracle = json.loads(oracle_path.read_text())
        reference_done = oracle['status'].startswith('complete')
        eligible = {r['task_id'] for r in oracle['rows'] if r.get('oracle_passed')}
        if reference_reservation and reference_done:
            pool.release(reference_reservation)
            reference_reservation = None
        for entry in list(pending):
            task = tasks[entry["task_id"]]
            # Finish the largest allowed remaining solver+grader interval
            # before the fixed cloud stop. Never add a truncated trial.
            if stopped or time.time() + task["agent_timeout_sec"] + task["verifier_timeout_sec"] + 600 >= deadline:
                entry["status"] = "not_started_admission_closed"
                pending.remove(entry)
                continue
            if entry['task_id'] not in eligible:
                if reference_done:
                    entry['status'] = 'environment_unavailable'
                    pending.remove(entry)
                continue
            entry['status'] = 'pending'
            if available_memory() < 6 * 1024**3 or shutil.disk_usage(root).free < 40 * 1024**3:
                stopped = True
                receipt["admission_stop_reason"] = "host_memory_or_disk_reserve"
                break
            name = f"tb-{label}-{entry['index']+1:03d}-{entry['task_id']}-{entry['arm']}"
            request = reservation(task, entry["arm"])
            if not pool.acquire(name, request):
                continue
            # Admission uses the immutable image checked during reference runs.
            image_lock = next(t for t in environment_lock['tasks'] if t['task_id'] == task['task_id'])
            check_environment(root, upstream, {'tasks': [image_lock]})
            job, config_path = root / "jobs" / name, root / "setup" / (name + ".json")
            if job.exists() or config_path.exists():
                pool.release(name)
                raise FileExistsError(name)
            config = {"job_name": name, "jobs_dir": str(root / "jobs"), "n_concurrent_trials": 1,
                      "environment": {"type": "docker", "delete": True},
                      "agents": [{"import_path": "harbor_agent:HermesSupergoalStudy", "model_name": "devin/swe-2",
                                  "kwargs": {"arm": entry["arm"], "max_requests": registration["max_upstream_requests_per_trial"],
                                             "max_episodes": registration["max_outer_episodes"],
                                             "budget_seconds": task["agent_timeout_sec"]}}],
                      "tasks": [{"path": str(upstream / "tasks" / entry["task_id"])}]}
            write_json(config_path, config)
            log = (root / "setup" / (name + ".log")).open("xb")
            try:
                proc = subprocess.Popen([str(root / "harbor-venv/bin/harbor"), "run", "--config", str(config_path)],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
            except BaseException:
                log.close()
                pool.release(name)
                raise
            entry.update(status="running", started_at=now(), job=str(job), harbor_pid=proc.pid, reservation=request)
            running[name] = (proc, log, entry)
            pending.remove(entry)
            write_json(receipt_path, receipt)
        for name, (proc, log, entry) in list(running.items()):
            if proc.poll() is None:
                continue
            log.close()
            entry.update(summarize(Path(entry["job"])), exit_code=proc.returncode, finished_at=now())
            pool.release(name)
            del running[name]
            # Preserve task/agent failures and continue the registered design.
            # A shared infrastructure incident stops new admissions for audit.
            if entry["status"] not in {"graded", "trial_exception"}:
                stopped = True
                receipt["admission_stop_reason"] = "verifier_apparatus_requires_audit"
        receipt["heartbeat_at"] = now()
        write_json(receipt_path, receipt)
        if pending or running:
            time.sleep(5)
    receipt.update(status="complete" if all(r["status"] == "graded" for r in receipt["rows"]) else "complete_with_failures_or_unavailable_rows",
                   finished_at=now())
    if reference_reservation:
        pool.release(reference_reservation)
    write_json(receipt_path, receipt)
    subprocess.run([str(root / 'harbor-venv/bin/python'), str(Path(__file__).with_name('finalize_registered.py')),
                    '--root', str(root), '--candidate', str(args.candidate), '--batch-receipt', str(receipt_path),
                    '--host', 'supergoal-gcp'], check=True)


if __name__ == "__main__":
    main()
