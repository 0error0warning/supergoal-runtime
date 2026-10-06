"""Finite, serial Linux operator runner for an already frozen public study.

This schedules only the rows in the registration, never generates extra seeds,
and never resumes or overwrites a started trial automatically. Run on grok-bot;
the model worker cannot see this file, the registration, or host control state.
"""

from __future__ import annotations

import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time

from collect_public import classify


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def available_memory():
    rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(rows["MemAvailable"].split()[0]) * 1024


def check_sources(candidate, registration, upstream):
    for name, expected in registration["source_sha256"].items():
        if hashlib.sha256((candidate / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f"Frozen candidate changed: {name}")
    for task in registration["tasks"]:
        data = (upstream / task["metadata_path"]).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if actual != task["metadata_git_blob_sha"]:
            raise ValueError(f"Official metadata changed: {task['task_id']}")


def check_environment(root, upstream, environment_lock):
    for task in environment_lock["tasks"]:
        directory = upstream / "tasks" / task["task_id"]
        current = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(directory.rglob("*")) if p.is_file()}
        if current != task["files_sha256"]:
            raise ValueError(f"Official task tree changed: {task['task_id']}")
        result = subprocess.run(["/usr/bin/docker", "--host", "unix://" + str(root / "run/docker.sock"),
                                 "image", "inspect", task["image_tag"], "--format", "{{.Id}}"],
                                check=True, capture_output=True, text=True)
        if result.stdout.strip() != task["image_id"]:
            raise ValueError(f"Official image tag changed: {task['task_id']}")


def summarize(job):
    """Conservative diagnostics; raw scores always survive apparatus errors."""
    trials = list(job.glob("*/result.json"))
    if len(trials) != 1:
        return {"status": "apparatus_incomplete", "trial_result_count": len(trials)}
    path = trials[0]
    result = json.loads(path.read_text())
    log = path.parent / "verifier/test-stdout.txt"
    output = log.read_text(errors="replace") if log.exists() else ""
    # These six tasks use pytest. A missing runner must not become a model zero.
    tests_completed = bool(re.search(r"=+ .*\b\d+ (?:passed|failed)\b.*=+", output))
    dependencies_failed = bool(re.search(
        r"Failed to fetch|Unable to fetch some archives|uvx: command not found|curl: command not found",
        output,
    ))
    verifier = result.get("verifier_result") or {}
    agent = result.get("agent_result") or {}
    exception = result.get("exception_info")
    status = classify(result, output)
    return {
        "status": status,
        "result_path": str(path),
        "raw_rewards": verifier.get("rewards"),
        "tests_completed_marker": tests_completed,
        "dependency_failure_marker": dependencies_failed,
        "exception_type": exception.get("exception_type") if exception else None,
        "agent_metadata": agent.get("metadata"),
        "n_input_tokens": agent.get("n_input_tokens"),
        "n_output_tokens": agent.get("n_output_tokens"),
        "n_cache_tokens": agent.get("n_cache_tokens"),
        "started_at": result.get("started_at"),
        "finished_at": result.get("finished_at"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--registration", type=Path, required=True)
    parser.add_argument("--environment-lock", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--stop", type=int)
    args = parser.parse_args()
    registration = json.loads(args.registration.read_text())
    label = registration["experiment"]
    if not re.fullmatch(r"[a-z0-9-]+", label):
        raise ValueError("Invalid registration name")
    root = args.root.resolve()
    upstream = root / "upstream/terminal-bench-2-1"
    environment_lock = json.loads(args.environment_lock.read_text())
    if environment_lock["experiment"] != label:
        raise ValueError("Environment lock belongs to another experiment")
    check_sources(args.candidate, registration, upstream)
    check_environment(root, upstream, environment_lock)
    plan = registration["planned_order"]
    stop = len(plan) if args.stop is None else args.stop
    if not 0 <= args.start < stop <= len(plan):
        raise ValueError("Invalid registered row interval")
    receipt_path = root / "setup" / f"{label}-rows-{args.start:02d}-{stop:02d}.json"
    if receipt_path.exists():
        raise FileExistsError("A runner receipt already exists for this interval")
    lock = (root / "run/registered-study.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    receipt = {"started_at": now(), "pid": os.getpid(), "candidate": str(args.candidate),
               "registration_sha256": hashlib.sha256(args.registration.read_bytes()).hexdigest(),
               "environment_lock_sha256": hashlib.sha256(args.environment_lock.read_bytes()).hexdigest(),
               "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "status": "running", "rows": []}
    write_json(receipt_path, receipt)
    env = dict(os.environ)
    env["DOCKER_HOST"] = "unix://" + str(root / "run/docker.sock")
    env["PYTHONPATH"] = os.pathsep.join(map(str, [args.candidate,
        args.candidate / "experiments", args.candidate / "experiments/public_benchmarks"]))
    tasks = {t["task_id"]: t for t in registration["tasks"]}
    try:
        for index in range(args.start, stop):
            row = plan[index]
            task = tasks[row["task_id"]]
            check_sources(args.candidate, registration, upstream)
            check_environment(root, upstream, environment_lock)
            mem, disk = available_memory(), shutil.disk_usage(root).free
            if mem < 4.5 * 1024**3 or disk < 15 * 1024**3:
                raise RuntimeError(f"Admission reserve unavailable: memory={mem}, disk={disk}")
            job_name = f"tb-{label}-{index+1:02d}-{row['task_id']}-{row['arm']}"
            job = root / "jobs" / job_name
            config_path = root / "setup" / (job_name + ".json")
            if job.exists() or config_path.exists():
                raise FileExistsError("A registered row was already started; audit it before resuming")
            config = {
                "job_name": job_name, "jobs_dir": str(root / "jobs"), "n_concurrent_trials": 1,
                "environment": {"type": "docker", "delete": False, "kwargs": {"keep_containers": True}},
                "agents": [{"import_path": "harbor_agent:HermesSupergoalStudy", "model_name": "devin/swe-2",
                            "kwargs": {"arm": row["arm"],
                                       "max_requests": registration["max_upstream_requests_per_trial"],
                                       "max_episodes": registration["max_outer_episodes"],
                                       "budget_seconds": task["agent_timeout_sec"]}}],
                "tasks": [{"path": str(upstream / "tasks" / row["task_id"])}],
            }
            write_json(config_path, config)
            current = {"index": index, **row, "job": str(job), "started_at": now(),
                       "admission_available_memory_bytes": mem, "admission_free_disk_bytes": disk,
                       "status": "running"}
            receipt["rows"].append(current)
            write_json(receipt_path, receipt)
            with (root / "setup" / (job_name + ".log")).open("xb") as log:
                proc = subprocess.Popen([str(root / "harbor-venv/bin/harbor"), "run", "--config", str(config_path)],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
                current["harbor_pid"] = proc.pid
                write_json(receipt_path, receipt)
                while proc.poll() is None:
                    time.sleep(20)
                    receipt["heartbeat_at"] = now()
                    write_json(receipt_path, receipt)
                current.update(summarize(job))
                current["exit_code"] = proc.returncode
                current["finished_at"] = now()
                write_json(receipt_path, receipt)
            # Do not continue through apparatus failures or quietly add a retry.
            if proc.returncode or current["status"] != "graded":
                raise RuntimeError(f"Registered row {index} requires audit: {current['status']}")
        receipt["status"] = "complete"
    except BaseException as exc:
        receipt["status"] = "stopped_for_audit"
        receipt["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        receipt["finished_at"] = now()
        write_json(receipt_path, receipt)
        lock.close()


if __name__ == "__main__":
    main()
