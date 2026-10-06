"""Export selected study receipts, without credentials or private Hermes source.

Run on the experiment server. Full model trajectories and filesystem snapshots
stay there; this export records their hashes and preserves official grader logs.
No collection step calls a model or changes a trial's original score.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess


def read_json(path):
    return json.loads(path.read_text()) if path.is_file() else None


def classify(result, stdout):
    completed = bool(re.search(r"=+ .*\b\d+ (?:passed|failed)\b.*=+", stdout))
    dependency = bool(re.search(
        r"Failed to fetch|Unable to fetch some archives|uvx: command not found|curl: command not found",
        stdout,
    ))
    collection_error = bool(re.search(r"ERROR collecting|error[s]? during collection", stdout))
    if dependency and not completed:
        return "apparatus_dependency_failure"
    if result.get("exception_info"):
        return "trial_exception"
    if collection_error or not completed:
        return "needs_verifier_audit"
    if (result.get("verifier_result") or {}).get("rewards") is None:
        return "apparatus_missing_reward"
    return "graded"


def collect(root, *, experiment=None, host="grok-bot"):
    output = {"schema": 1, "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "host": host, "raw_scores_unchanged": True, "trials": [], "setup": {}, "model_runs": {}}
    prefixes = (f"tb-{experiment}-", f"tb-oracle-{experiment}-") if experiment else ("tb-public-dev", "tb-oracle-")
    for job in sorted((root / "jobs").iterdir()):
        if not job.is_dir() or not job.name.startswith(prefixes):
            continue
        for path in sorted(job.glob("*/result.json")):
            result = read_json(path)
            logs = {}
            for name in ["test-stdout.txt", "test-stderr.txt", "reward.txt"]:
                log = path.parent / "verifier" / name
                if log.is_file():
                    # Preserve bounded public verifier logs, never worker config.
                    logs[name] = log.read_text(errors="replace")
            info = {"job_name": job.name, "result_path": str(path), "result": result,
                    "classification": classify(result, logs.get("test-stdout.txt", "")), "verifier_logs": logs}
            output["trials"].append(info)
            metadata = (result.get("agent_result") or {}).get("metadata") or {}
            control_id = metadata.get("control_id", "")
            if not re.fullmatch(r"[a-f0-9]{32}", control_id):
                continue
            control = root / "control" / control_id
            run = {"report": read_json(control / "report.json"),
                   "environment": read_json(control / "environment.json"),
                   "environment_bootstrap": read_json(control / "environment-bootstrap.json"),
                   "trajectory_receipts": []}
            for trace in sorted(control.glob("*-*/result.json")):
                data = trace.read_bytes()
                messages = json.loads(data).get("messages", [])
                tool_counts = {}
                for message in messages:
                    if message.get("role") == "tool":
                        name = message.get("name") or "tool"
                        tool_counts[name] = tool_counts.get(name, 0) + 1
                run["trajectory_receipts"].append({"path": str(trace), "sha256": hashlib.sha256(data).hexdigest(),
                                                   "bytes": len(data), "tool_result_counts": tool_counts})
            output["model_runs"][control_id] = run
    names = {"direct-model-tunnel.json", "container-preflight-repaired.json", "container-preflight.json",
             "fuse-config-restoration.json", "terminal-bench-2-1-source-archive.json"}
    for pattern in ["tb-oracle-*-start.json", "tb-oracle-*-receipt.json", "public-dev*-rows-*.json",
                    "environment-public-dev*.json"]:
        names.update(p.name for p in (root / "setup").glob(pattern))
    if experiment:
        for pattern in [f"{experiment}-rows-*.json", f"environment-{experiment}.json"]:
            names.update(p.name for p in (root / "setup").glob(pattern))
    for name in sorted(names):
        value = read_json(root / "setup" / name)
        if value is not None:
            output["setup"][name] = value
    for label in ["tool-smoke01", "tool-smoke-ubuntu03"]:
        value = read_json(root / "control" / label / "result.json")
        if value is not None:
            output["setup"][label] = value
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--experiment")
    parser.add_argument("--host", default="grok-bot")
    parser.add_argument("--inspect-checkpoints", action="store_true",
                        help="Read current experiment Docker image availability and retention receipts")
    args = parser.parse_args()
    bundle = collect(args.root, experiment=args.experiment, host=args.host)
    if args.inspect_checkpoints:
        image_ids = set(subprocess.check_output([
            "docker", "-H", "unix://" + str(args.root / "run/docker.sock"),
            "image", "ls", "--no-trunc", "--quiet"], text=True, timeout=30).splitlines())
        retention = (read_json(args.root / "setup/checkpoint-retention01.json") or {}).get("images", {})
        bundle["checkpoint_observed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        bundle["checkpoint_observations"] = {}
        for run in bundle["model_runs"].values():
            image_id = (run.get("report") or {}).get("artifact_image_id")
            if image_id:
                record = retention.get(image_id, {})
                bundle["checkpoint_observations"][image_id] = {
                    "image_available": image_id in image_ids,
                    "retention_status": record.get("status", "unobserved"),
                    "manifest_sha256": record.get("manifest_sha256"),
                    "blobs_reverified_at_collection": False,
                }
    data = json.dumps(bundle, ensure_ascii=False, indent=2)
    # Defense in depth for exception messages in otherwise allowlisted files.
    credential = read_json(args.root / "private/model.json") or {}
    key = credential.get("api_key")
    if key:
        data = data.replace(key, "[REDACTED]")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".tmp")
    temp.write_text(data)
    temp.replace(args.output)
    print(json.dumps({"output": str(args.output), "bytes": len(data.encode()),
                      "sha256": hashlib.sha256(data.encode()).hexdigest()}))


if __name__ == "__main__":
    main()
