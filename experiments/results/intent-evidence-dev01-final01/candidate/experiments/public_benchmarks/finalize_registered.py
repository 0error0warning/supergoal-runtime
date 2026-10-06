"""Finish one finite experiment workflow with a report even if the PC disconnects.

This is not a recurring scheduler and does not retry model trials. It waits only
for the already-started registered batch, then exports complete or partial data.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--batch-receipt", type=Path, required=True)
    parser.add_argument("--host", default="grok-bot")
    args = parser.parse_args()
    deadline = time.monotonic() + 24 * 3600
    reason = "batch_finished"
    while True:
        state = json.loads(args.batch_receipt.read_text(encoding="utf-8"))
        if state["status"] != "running":
            break
        timestamp = state.get("heartbeat_at", state["started_at"])
        age = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(timestamp)).total_seconds()
        if age > 180:
            reason = "controller_heartbeat_stale; partial report, no trial retries"
            break
        if time.monotonic() >= deadline:
            reason = "report_wait_limit_reached; partial report, no trial retries"
            break
        time.sleep(30)
    export = args.root / "exports"
    data = export / "experiments/results"
    docs = export / "docs"
    data.mkdir(parents=True, exist_ok=True)
    docs.mkdir(parents=True, exist_ok=True)
    operator = Path(__file__).parent
    label = args.candidate.name
    bundle = data / f"{label}-bundle.json"
    subprocess.run([sys.executable, str(operator / "collect_public.py"), "--root", str(args.root),
                    "--output", str(bundle), "--experiment", label, "--host", args.host], check=True)
    registration = args.candidate / "experiments/public_benchmarks" / f"registration-{label}.json"
    subprocess.run([sys.executable, str(operator / "analyze_public.py"), "--bundle", str(bundle),
                    "--registration", str(registration), "--json-output", str(data / f"{label}-analysis.json"),
                    "--markdown-output", str(docs / f"{label}-study.md")], check=True)
    receipt = {"finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "reason": reason,
               "batch_status": state["status"], "model_calls": 0, "new_trials_or_retries": 0,
               "source_sha256": {name: hashlib.sha256((operator / name).read_bytes()).hexdigest()
                                 for name in ["finalize_registered.py", "collect_public.py", "analyze_public.py"]}}
    (args.root / "setup" / f"{label}-final-report.json").write_text(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
