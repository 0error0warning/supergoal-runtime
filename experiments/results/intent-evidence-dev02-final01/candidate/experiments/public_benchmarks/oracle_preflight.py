"""Run Harbor's official reference solution after the registered transport repair.

This operator-only entry point is never imported by the model worker. It does not
change the official solution or verifier and makes no model requests.
"""

import json
from pathlib import Path

from harbor.agents.oracle import OracleAgent
from harbor.models.trial.paths import TrialPaths

from environment_bootstrap import APT_HTTPS_BOOTSTRAP, parse_bootstrap


class HttpsOracle(OracleAgent):
    def __init__(self, logs_dir, *, task_dir, **kwargs):
        # Harbor injects these arguments only for its built-in agent name.
        # A custom import path receives the explicit official task directory;
        # its TrialPaths still come from Harbor's real per-trial logs directory.
        super().__init__(logs_dir=logs_dir, task_dir=Path(task_dir),
                         trial_paths=TrialPaths(trial_dir=Path(logs_dir).parent), **kwargs)

    def version(self):
        return "official-oracle-with-apt-https-2"

    async def setup(self, environment):
        result = await environment.exec(
            APT_HTTPS_BOOTSTRAP,
            timeout_sec=15,
            user="root",
        )
        (self._trial_paths.agent_dir / "transport-preflight-process.json").write_text(json.dumps({
            "return_code": result.return_code, "stdout": result.stdout, "stderr": result.stderr}))
        if result.return_code:
            raise RuntimeError("APT HTTPS transport preflight failed")
        receipt = {
            "model_calls": 0,
            "adaptation": parse_bootstrap(result.stdout),
            "official_solution_and_tests_modified": False,
        }
        (self._trial_paths.agent_dir / "transport-preflight.json").write_text(
            json.dumps(receipt, indent=2)
        )


def main():
    import argparse
    import os
    import subprocess

    from run_registered import check_sources, now, summarize, write_json

    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/var/lib/supergoal-lab"))
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    root = args.root
    registration = json.loads((args.candidate / "experiments/public_benchmarks" / f"registration-{args.candidate.name}.json").read_text())
    upstream = root / "upstream/terminal-bench-2-1"
    check_sources(args.candidate, registration, upstream)
    receipt_path = root / "setup" / (args.label + "-receipt.json")
    if receipt_path.exists():
        raise FileExistsError(receipt_path)
    receipt = {"started_at": now(), "model_calls": 0, "status": "running", "rows": []}
    env = dict(os.environ)
    env["DOCKER_HOST"] = "unix://" + str(root / "run/docker.sock")
    env["PYTHONPATH"] = os.pathsep.join(map(str, [Path(__file__).parent,
                                        args.candidate / "experiments/public_benchmarks"]))
    write_json(receipt_path, receipt)
    try:
        for index, task in enumerate(registration["tasks"]):
            job_name = f"{args.label}-{index+1:02d}-{task['task_id']}"
            config_path = root / "setup" / (job_name + ".json")
            if config_path.exists():
                raise FileExistsError(config_path)
            task_dir = str(upstream / "tasks" / task["task_id"])
            config = {"job_name": job_name, "jobs_dir": str(root / "jobs"), "n_concurrent_trials": 1,
                      "environment": {"type": "docker", "delete": False},
                      "agents": [{"import_path": "oracle_preflight:HttpsOracle", "kwargs": {
                          "task_dir": task_dir, "agent_timeout_sec": task["agent_timeout_sec"]}}],
                      "tasks": [{"path": task_dir}]}
            write_json(config_path, config)
            with (root / "setup" / (job_name + ".log")).open("xb") as log:
                result = subprocess.run([str(root / "harbor-venv/bin/harbor"), "run", "--config", str(config_path)],
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
            summary = {"task_id": task["task_id"], **summarize(root / "jobs" / job_name), "exit_code": result.returncode}
            receipt["rows"].append(summary)
            write_json(receipt_path, receipt)
            if result.returncode or summary["status"] != "graded" or summary["raw_rewards"] != {"reward": 1.0}:
                raise RuntimeError(f"Oracle preflight needs audit: {task['task_id']}")
        receipt["status"] = "complete"
    finally:
        if receipt["status"] != "complete":
            receipt["status"] = "stopped_for_audit"
        receipt["finished_at"] = now()
        write_json(receipt_path, receipt)


if __name__ == "__main__":
    main()
