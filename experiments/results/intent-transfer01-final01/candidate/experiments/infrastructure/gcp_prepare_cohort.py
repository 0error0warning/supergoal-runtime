"""Pull and freeze public task environments before any GCP model outcome."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys


ROOT = Path("/var/lib/supergoal-lab")
LABEL = "public-gcp01"
CANDIDATE = ROOT / "candidates" / LABEL


def main():
    sys.path.insert(0, str(ROOT / "study-operator"))
    from run_registered import check_sources

    registration = CANDIDATE / "experiments/public_benchmarks" / f"registration-{LABEL}.json"
    reg = json.loads(registration.read_text())
    upstream = ROOT / "upstream/terminal-bench-2-1"
    check_sources(CANDIDATE, reg, upstream)
    docker = ["/usr/bin/docker", "--host", "unix://" + str(ROOT / "run/docker.sock")]
    target = ROOT / "setup" / f"environment-{LABEL}.json"
    if target.exists():
        raise FileExistsError(target)
    runtime = json.loads((ROOT / "setup/gcp-runtime.json").read_text())
    if runtime["status"] != "complete":
        raise ValueError("Runtime installation incomplete")
    state = {"schema": 1, "experiment": LABEL, "host": "supergoal-gcp",
             "kernel": platform.release(), "platform": platform.platform(),
             "harbor_version": "0.24.0", "hermes_version": "0.21.3",
             "hermes_host_python": "3.13.5", "tasks": [], "model_trials_started": 0,
             "registration_sha256": hashlib.sha256(registration.read_bytes()).hexdigest(),
             "runtime_receipt_sha256": hashlib.sha256((ROOT / "setup/gcp-runtime.json").read_bytes()).hexdigest(),
             "docker_version": json.loads(subprocess.check_output(docker + ["version", "--format", "{{json .}}"], text=True)),
             "compose_version": subprocess.check_output(docker + ["compose", "version", "--short"], text=True).strip()}
    for task in reg["tasks"]:
        print("Preparing " + task["task_id"], flush=True)
        tag = task["docker_image_tag"]
        subprocess.run(docker + ["pull", tag], check=True, timeout=1800)
        info = json.loads(subprocess.check_output(docker + ["image", "inspect", tag], text=True))[0]
        directory = upstream / "tasks" / task["task_id"]
        state["tasks"].append({"task_id": task["task_id"], "image_tag": tag,
            "image_id": info["Id"], "repo_digests": info["RepoDigests"],
            "image_size_bytes": info["Size"],
            "files_sha256": {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in sorted(directory.rglob("*")) if p.is_file()}})
    state["frozen_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with target.open("x") as handle:
        json.dump(state, handle, indent=2)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(CANDIDATE / "experiments/public_benchmarks")
    subprocess.run([ROOT / "harbor-venv/bin/python", ROOT / "study-operator/oracle_preflight.py",
                    "--candidate", CANDIDATE, "--label", "tb-oracle-" + LABEL],
                   env=env, check=True, timeout=18000)


if __name__ == "__main__":
    main()
