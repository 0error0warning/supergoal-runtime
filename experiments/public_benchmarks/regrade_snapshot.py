"""Regrade a retained development artifact without another model call.

Keeps the original Harbor grader logs untouched. The only deliberate environment
repair is recorded Debian repository HTTPS transport. Not a new agent trial.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import time
from pathlib import Path

from environment_bootstrap import APT_HTTPS_BOOTSTRAP


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--artifact", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.control_id) or not re.fullmatch(r"[a-z0-9-]+", args.name):
        parser.error("invalid identity")
    root = Path("/var/lib/supergoal-lab")
    record = json.loads((root / "control" / args.control_id / "environment.json").read_text())
    base = ["docker", "-H", "unix://" + str(root / "run/docker.sock")]
    image = "sg-regrade:" + args.name
    out = root / "regrades" / args.name
    out.mkdir(parents=True, exist_ok=False)
    (out / "verifier").mkdir()
    subprocess.run(base + ["commit", record["container_id"], image], check=True, stdout=subprocess.DEVNULL)
    receipt = {"control_id": args.control_id, "model_calls": 0, "source_container": record["container_id"],
               "started": time.time(), "snapshot_image": image, "artifact": args.artifact,
               "bootstrap_sha256": hashlib.sha256(APT_HTTPS_BOOTSTRAP.encode()).hexdigest()}
    script = ("set -eu\nsha256sum -- " + shlex.quote(args.artifact) + " > /logs/verifier/artifact-before.sha256\n"
              "(\n" + APT_HTTPS_BOOTSTRAP + "\n) > /logs/verifier/environment-repair.tsv\n"
              "sha256sum /tests/test.sh > /logs/verifier/grader-script.sha256\n"
              "bash /tests/test.sh\nsha256sum -- " + shlex.quote(args.artifact) + " > /logs/verifier/artifact-after.sha256\n")
    name = "sg-regrade-" + args.name
    with (out / "regrade.log").open("wb") as log:
        process = subprocess.run(base + ["run", "--name", name, "--cpus", "1", "--memory", "2g",
                    "--memory-swap", "2g", "--volume", str(out / "verifier") + ":/logs/verifier",
                    "--entrypoint", "/bin/bash", image, "-c", script],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, timeout=900)
    receipt.update(returncode=process.returncode, finished=time.time())
    before, after = out / "verifier/artifact-before.sha256", out / "verifier/artifact-after.sha256"
    receipt["artifact_unchanged"] = after.exists() and before.read_bytes() == after.read_bytes()
    reward = out / "verifier/reward.txt"
    receipt["reward"] = reward.read_text().strip() if reward.exists() else None
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2))
    print(json.dumps(receipt), flush=True)
    # The image/container are retained until the operator has audited this
    # regrade. Original trial logs and the original container are unchanged.


if __name__ == "__main__":
    main()
