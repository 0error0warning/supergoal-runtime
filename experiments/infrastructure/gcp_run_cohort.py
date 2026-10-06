"""Run one gated, finite registered batch and export its complete/partial report."""

import datetime
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path("/var/lib/supergoal-lab")
LABEL = "public-gcp01"


def main():
    candidate = ROOT / "candidates" / LABEL
    registration = candidate / "experiments/public_benchmarks" / f"registration-{LABEL}.json"
    reg = json.loads(registration.read_text())
    oracle = json.loads((ROOT / "setup" / f"tb-oracle-{LABEL}-receipt.json").read_text())
    smoke = json.loads((ROOT / "setup/gcp-hermes-smoke01.json").read_text())
    if (oracle["status"] != "complete" or len(oracle["rows"]) != len(reg["tasks"])
            or any(r["status"] != "graded" or r["raw_rewards"] != {"reward": 1.0} for r in oracle["rows"])
            or [r["task_id"] for r in oracle["rows"]] != [r["task_id"] for r in reg["tasks"]]
            or smoke["status"] != "passed"
            or smoke["tool_results"]["timeout"]["exit_code"] != 124
            or not smoke["tool_results"]["hidden_read"].get("error")):
        raise RuntimeError("Start gate has not passed")
    stop_at = datetime.datetime.fromisoformat(reg["cloud_stop_at_utc"].replace("Z", "+00:00"))
    if (stop_at - datetime.datetime.now(datetime.timezone.utc)).total_seconds() < 21 * 3600:
        raise RuntimeError("Insufficient room before cloud stop for this full serial batch; audit schedule")
    environment = ROOT / "setup" / f"environment-{LABEL}.json"
    operator = ROOT / "study-operator-gcp01"
    python = ROOT / "harbor-venv/bin/python"
    receipt_path = ROOT / "setup" / f"{LABEL}-workflow.json"
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    state = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "status": "running",
             "automatic_trial_retries": 0, "host": "supergoal-gcp",
             "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in sorted(operator.glob("*.py"))}}
    receipt_path.write_text(json.dumps(state, indent=2))
    try:
        result = subprocess.run([python, operator / "run_registered.py", "--candidate", candidate,
            "--registration", registration, "--environment-lock", environment], check=False)
        state["batch_exit_code"] = result.returncode
        batch = ROOT / "setup" / f"{LABEL}-rows-00-{len(reg['planned_order']):02d}.json"
        if batch.exists():
            subprocess.run([python, operator / "finalize_registered.py", "--candidate", candidate,
                "--batch-receipt", batch, "--host", "supergoal-gcp"], check=True)
            state["batch_status"] = json.loads(batch.read_text())["status"]
        state["status"] = "complete" if result.returncode == 0 else "stopped_for_audit"
        if result.returncode:
            raise RuntimeError("Registered batch stopped; raw failure and partial report preserved")
    except BaseException as exc:
        state.update(status="stopped_for_audit", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt_path.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
