"""Fetch the pinned OneDayAgent source and its documented environment image."""

import datetime
import json
from pathlib import Path
import subprocess


ROOT = Path("/var/lib/supergoal-lab")
COMMIT = "f29f6d496437c9ea7b8b5e8fe232d186b868b9f5"


def main():
    receipt = ROOT / "setup/oneday-assets01.json"
    if receipt.exists():
        raise FileExistsError(receipt)
    state = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "status": "running", "model_calls": 0}
    receipt.write_text(json.dumps(state, indent=2))
    try:
        upstream = ROOT / "upstream/onedayagent"
        subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout",
            "https://github.com/zjunlp/OneDayAgent.git", upstream], check=True, timeout=900)
        subprocess.run(["git", "-C", upstream, "checkout", "--detach", COMMIT], check=True, timeout=900)
        state["source_commit"] = subprocess.check_output(["git", "-C", upstream, "rev-parse", "HEAD"], text=True).strip()
        if state["source_commit"] != COMMIT:
            raise ValueError("OneDayAgent source mismatch")
        state["phase"] = "pulling_environment"
        receipt.write_text(json.dumps(state, indent=2))
        docker = ["docker", "--host", "unix://" + str(ROOT / "run/docker.sock")]
        tag = "johnsonzheng03/onedayagent:dev"
        subprocess.run(docker + ["pull", tag], check=True, timeout=3600)
        image = json.loads(subprocess.check_output(docker + ["image", "inspect", tag], text=True))[0]
        state.update(image_tag=tag, image_id=image["Id"], repo_digests=image["RepoDigests"],
                     image_size_bytes=image["Size"], status="complete")
    except BaseException as exc:
        state.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        state["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt.write_text(json.dumps(state, indent=2))


if __name__ == "__main__":
    main()
