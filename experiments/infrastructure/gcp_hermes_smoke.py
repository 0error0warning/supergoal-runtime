"""Exercise real Hermes tools and SWE2 in a disposable, empty container."""

import datetime
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path("/var/lib/supergoal-lab")
HERE = ROOT / "candidates/public-gcp01/experiments/public_benchmarks"
SOURCE = ROOT / "private/runtime/hermes-v0.21.3-251bedc-mt2"


def main():
    sys.path.insert(0, str(HERE.parent))
    from model_proxy import ModelProxy

    directory = ROOT / "control/gcp-hermes-smoke01"
    directory.mkdir(mode=0o700)
    docker = ["/usr/bin/docker", "--host", "unix://" + str(ROOT / "run/docker.sock")]
    # This empty official image contains no task solution or final verifier.
    image = json.loads(subprocess.check_output(docker + ["image", "inspect",
        "alexgshaw/configure-git-webserver:20251031"], text=True))[0]["Id"]
    container = subprocess.check_output(docker + ["run", "-d", "--network", "none",
        "--cpus", "1", "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "256",
        "--label", "supergoal.preflight=gcp-hermes-smoke01", "--workdir", "/app",
        "--entrypoint", "/bin/sh", image, "-c", "sleep infinity"], text=True).strip()
    marker = directory / "host-marker.txt"
    marker.write_text("HOST_ONLY_SG_MARKER")
    receipt = {"started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
               "model": "devin/swe-2", "container": container, "image_id": image,
               "official_task_outcome": False, "status": "running"}
    try:
        provider = json.loads((ROOT / "private/model.json").read_text())
        with ModelProxy(provider, limit=4) as proxy:
            for kind in ["tools", "model"]:
                work = directory / kind
                work.mkdir()
                descriptor = work / "environment.json"
                descriptor.write_text(json.dumps({"docker_host": docker[2], "container_id": container, "user": "root"}))
                control = {"provider": proxy.config(), "cwd": "/app", "max_calls": 4,
                    "seconds": 150, "session_id": "gcp-smoke-" + kind, "output": str(work / "result.json"),
                    "host_marker": str(marker), "prompt": "Use the terminal tool to execute printf SG_SWE2_GCP_READY. Then reply with that exact marker and stop."}
                (work / "control.json").write_text(json.dumps(control))
                env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(work),
                    "LANG": "C.UTF-8", "HERMES_HOME": str(work / "hermes-home"),
                    "PYTHONPATH": os.pathsep.join([str(HERE), str(SOURCE)]),
                    "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "HERMES_YOLO": "1",
                    "TERMINAL_ENV": "harbor_task", "TERMINAL_CWD": "/app",
                    "SUPERGOAL_HARBOR_DESCRIPTOR": str(descriptor),
                    "HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS": "90", "HERMES_CODEX_TTFB_TIMEOUT_SECONDS": "90"}
                args = [str(ROOT / "hermes-venv/bin/python"), str(HERE / "hermes_episode.py"), str(work / "control.json")]
                if kind == "tools":
                    args.append("--tool-smoke")
                with (work / "worker.log").open("xb") as log:
                    subprocess.run(args, env=env, cwd=work, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=log, check=True, timeout=180)
                result = json.loads((work / "result.json").read_text())
                if kind == "tools":
                    probes = result["tool_smoke"]
                    if ("isolated" not in str(probes["terminal"])
                            or "hermes_bridge_smoke" not in str(probes["read"])
                            or probes["timeout"].get("exit_code") != 124
                            or not probes["hidden_read"].get("error")
                            or "HOST_ONLY_SG_MARKER" in str(probes["hidden_read"])
                            or proxy.records):
                        raise ValueError("Hermes tool isolation smoke failed")
                    receipt["tool_results"] = probes
                else:
                    if (not result.get("completed")
                            or "SG_SWE2_GCP_READY" not in str(result.get("final_response"))
                            or not any(m.get("role") == "tool" for m in result.get("messages", []))
                            or not proxy.records
                            or any(r.get("http_status") != 200 for r in proxy.records)):
                        raise ValueError("Real SWE2 tool smoke failed")
                    receipt["model_completed"] = result["completed"]
            receipt["requests"] = proxy.records
            receipt["physical_model_requests"] = len(proxy.records)
        receipt["status"] = "passed"
    except BaseException as exc:
        receipt.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        subprocess.run(docker + ["rm", "-f", container], check=True)
        receipt["finished_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (ROOT / "setup/gcp-hermes-smoke01.json").write_text(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
