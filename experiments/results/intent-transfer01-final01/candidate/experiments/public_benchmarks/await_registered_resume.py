"""Wait for capacity, then continue a reviewed finite study without retrying rows.

This operator does not change the frozen agent, budgets, or admission threshold.
The failure audit must explicitly authorize the next unstarted registered row.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_json(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def validate_resume(audit, previous, registration, start, stop):
    if audit.get("disposition") != "retain_failure_continue_unstarted":
        raise ValueError("A closed failure audit is required")
    if audit.get("candidate") != registration["experiment"]:
        raise ValueError("Audit candidate mismatch")
    if previous.get("status") != "stopped_for_audit":
        raise ValueError("Previous batch must have stopped")
    last = previous["rows"][-1]
    if last["index"] + 1 != start or audit.get("next_registered_index") != start:
        raise ValueError("Only the immediately following unstarted row may resume")
    metadata = last.get("agent_metadata") or {}
    if metadata.get("control_id") != audit.get("control_id"):
        raise ValueError("Audit does not describe the failed trial")
    if not 0 < start < stop <= len(registration["planned_order"]):
        raise ValueError("Invalid registered interval")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--previous-receipt", type=Path, required=True)
    parser.add_argument("--start", type=int, required=True)
    parser.add_argument("--stop", type=int, required=True)
    parser.add_argument("--max-wait-seconds", type=int, default=21600)
    args = parser.parse_args()
    if args.max_wait_seconds <= 0:
        raise ValueError("A finite positive wait is required")
    root = args.root.resolve()
    label = args.candidate.name
    registration_path = args.candidate / "experiments/public_benchmarks" / f"registration-{label}.json"
    registration = json.loads(registration_path.read_text())
    audit = json.loads(args.audit.read_text())
    previous = json.loads(args.previous_receipt.read_text())
    validate_resume(audit, previous, registration, args.start, args.stop)
    if hashlib.sha256(registration_path.read_bytes()).hexdigest() != previous["registration_sha256"]:
        raise ValueError("Frozen registration changed")
    operator = root / "study-operator"
    if hashlib.sha256((operator / "run_registered.py").read_bytes()).hexdigest() != previous["runner_sha256"]:
        raise ValueError("Frozen runner changed")
    batch = root / "setup" / f"{label}-rows-{args.start:02d}-{args.stop:02d}.json"
    if batch.exists():
        raise FileExistsError("The continuation interval was already started")
    receipt_path = root / "setup" / f"{label}-resume-{args.start:02d}-{args.stop:02d}.json"
    receipt = {
        "started_at": now(), "pid": os.getpid(), "status": "waiting_for_capacity", "start": args.start, "stop": args.stop,
        "audit_sha256": hashlib.sha256(args.audit.read_bytes()).hexdigest(),
        "operator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "min_available_memory_bytes": int(4.5 * 1024**3), "min_free_disk_bytes": 15 * 1024**3,
        "max_wait_seconds": args.max_wait_seconds, "retries_of_started_trials": 0,
        "batch_receipt": str(batch),
    }
    # Exclusive creation prevents two admission waiters for the same interval.
    with receipt_path.open("x", encoding="utf-8") as stream:
        json.dump(receipt, stream, indent=2)
    monotonic_deadline = time.monotonic() + args.max_wait_seconds
    realtime_deadline = time.time() + args.max_wait_seconds
    try:
        while True:
            mem = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            available = int(mem["MemAvailable"].split()[0]) * 1024
            free = shutil.disk_usage(root).free
            receipt.update(heartbeat_at=now(), available_memory_bytes=available, free_disk_bytes=free)
            ready = available >= receipt["min_available_memory_bytes"] and free >= receipt["min_free_disk_bytes"]
            if ready:
                provider = json.loads((root / "private/model.json").read_text())
                request = urllib.request.Request(provider["base_url"].rstrip("/") + "/models",
                    headers={"Authorization": "Bearer " + provider["api_key"]})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        ready = response.status == 200 and any(
                            row.get("id") == "devin/swe-2" for row in json.load(response).get("data", []))
                    receipt["model_endpoint_ready"] = ready
                except Exception as exc:
                    ready = False
                    receipt["model_endpoint_ready"] = False
                    receipt["endpoint_error_type"] = type(exc).__name__
            write_json(receipt_path, receipt)
            if time.monotonic() >= monotonic_deadline or time.time() >= realtime_deadline:
                receipt["status"] = "admission_wait_expired"
                return
            if ready:
                break
            time.sleep(30)
        command = [sys.executable, str(operator / "run_registered.py"),
                   "--root", str(root), "--candidate", str(args.candidate),
                   "--registration", str(registration_path),
                   "--environment-lock", str(root / "setup" / f"environment-{label}.json"),
                   "--start", str(args.start), "--stop", str(args.stop)]
        with (root / "setup" / f"{label}-resume-{args.start:02d}-{args.stop:02d}.log").open("xb") as log:
            proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
            receipt.update(status="running", runner_pid=proc.pid, admitted_at=now())
            while True:
                receipt["heartbeat_at"] = now()
                write_json(receipt_path, receipt)
                try:
                    receipt["runner_exit_code"] = proc.wait(timeout=20)
                    break
                except subprocess.TimeoutExpired:
                    pass
        receipt["status"] = "complete" if proc.returncode == 0 else "stopped_for_audit"
        data = root / "exports/experiments/results"
        docs = root / "exports/docs"
        data.mkdir(parents=True, exist_ok=True)
        docs.mkdir(parents=True, exist_ok=True)
        bundle = data / "public-benchmarks-2026-10-05.json"
        subprocess.run([sys.executable, str(operator / "collect_public.py"), "--root", str(root),
                        "--output", str(bundle)], check=True, timeout=60)
        subprocess.run([sys.executable, str(operator / "analyze_public.py"), "--bundle", str(bundle),
                        "--registration", str(registration_path),
                        "--json-output", str(data / f"{label}-analysis.json"),
                        "--markdown-output", str(docs / "public-benchmark-study-2026-10-05.md")],
                       check=True, timeout=60)
        receipt["report_exported_at"] = now()
    except BaseException as exc:
        receipt.update(status="operator_error", error_type=type(exc).__name__)
        raise
    finally:
        receipt["finished_at"] = now()
        write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
