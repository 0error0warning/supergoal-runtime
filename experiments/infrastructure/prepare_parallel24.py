"""Finite, model-free official environment/reference checks for 24 fixed IDs."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path("/var/lib/supergoal-lab")
LABEL = "parallel24"
sys.path.insert(0, str(ROOT / "oracle-operator-parallel24"))
from run_registered import check_sources, now, summarize, write_json  # noqa: E402


def main():
    selection_path = ROOT / "setup/selection-parallel24.json"
    selection = json.loads(selection_path.read_text())
    upstream = ROOT / "upstream/terminal-bench-2-1"
    check_sources(ROOT, {"source_sha256": {}, "tasks": selection["tasks"]}, upstream)
    actual = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    if actual != selection["official_source_commit"]:
        raise ValueError("Upstream commit changed")
    receipt_path = ROOT / "setup/tb-oracle-parallel24-receipt.json"
    lock_path = ROOT / "setup/environment-parallel24.json"
    if receipt_path.exists() or lock_path.exists():
        raise FileExistsError("Preserve the original preparation; explicit audit required")
    docker = ["/usr/bin/docker", "--host", "unix://" + str(ROOT / "run/docker.sock")]
    env = dict(os.environ, DOCKER_HOST=docker[-1], PYTHONPATH=str(ROOT / "oracle-operator-parallel24"))
    state = {"status": "preparing", "started_at": now(), "model_calls": 0,
             "selection_sha256": hashlib.sha256(selection_path.read_bytes()).hexdigest(),
             "max_concurrent_oracles": 2, "cpu_pool": 4, "rows": []}
    lock = {"experiment": LABEL, "tasks": [], "selection_sha256": state["selection_sha256"]}
    write_json(receipt_path, state)
    ready = []
    for index, task in enumerate(selection["tasks"]):
        row = {"index": index, "task_id": task["task_id"], "status": "preparing_image"}
        state["rows"].append(row)
        write_json(receipt_path, state)
        try:
            if shutil.disk_usage(ROOT).free < 45 * 1024**3:
                raise RuntimeError("45 GiB host disk reserve unavailable")
            with (ROOT / "setup" / f"pull-parallel24-{task['task_id']}.log").open("xb") as log:
                subprocess.run(docker + ["pull", task["docker_image_tag"]], check=True,
                               stdout=log, stderr=log, timeout=1800)
            info = json.loads(subprocess.check_output(docker + ["image", "inspect", task["docker_image_tag"]]))[0]
            directory = upstream / "tasks" / task["task_id"]
            lock["tasks"].append({"task_id": task["task_id"], "image_tag": task["docker_image_tag"],
                "image_id": info["Id"], "repo_digests": info["RepoDigests"], "image_size_bytes": info["Size"],
                "files_sha256": {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p in sorted(directory.rglob("*")) if p.is_file()}})
            ready.append((index, task, row))
            row["status"] = "image_ready"
        except Exception as exc:
            row.update(status="environment_unavailable", error=f"{type(exc).__name__}: {exc}"[:1000])
        write_json(receipt_path, state)
        write_json(lock_path, lock)
    lock["frozen_at"] = now()
    write_json(lock_path, lock)
    state["status"] = "oracle_checks"

    def oracle(index, task):
        name = f"tb-oracle-parallel24-{index+1:02d}-{task['task_id']}"
        config_path = ROOT / "setup" / (name + ".json")
        if config_path.exists() or (ROOT / "jobs" / name).exists():
            raise FileExistsError(name)
        config = {"job_name": name, "jobs_dir": str(ROOT / "jobs"), "n_concurrent_trials": 1,
                  "environment": {"type": "docker", "delete": True},
                  "agents": [{"import_path": "oracle_preflight:HttpsOracle", "kwargs": {
                      "task_dir": str(upstream / "tasks" / task["task_id"]),
                      "agent_timeout_sec": task["agent_timeout_sec"]}}],
                  "tasks": [{"path": str(upstream / "tasks" / task["task_id"])}]}
        write_json(config_path, config)
        with (ROOT / "setup" / (name + ".log")).open("xb") as log:
            proc = subprocess.run([str(ROOT / "harbor-venv/bin/harbor"), "run", "--config", str(config_path)],
                                  stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env)
        return {**summarize(ROOT / "jobs" / name), "exit_code": proc.returncode, "finished_at": now()}

    running = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        while ready or running:
            used_cpu = sum(t[1]["resources"]["cpus"] for t in running.values())
            for entry in list(ready):
                index, task, row = entry
                if len(running) >= 2 or used_cpu + task["resources"]["cpus"] > 4:
                    continue
                row.update(status="oracle_running", started_at=now())
                running[executor.submit(oracle, index, task)] = entry
                used_cpu += task["resources"]["cpus"]
                ready.remove(entry)
            for future in list(running):
                if not future.done():
                    continue
                _, _, row = running.pop(future)
                try:
                    result = future.result()
                    row.update(result)
                    row["oracle_passed"] = (result["exit_code"] == 0 and result["status"] == "graded"
                                            and result["raw_rewards"] == {"reward": 1.0})
                except Exception as exc:
                    row.update(status="oracle_exception", error=f"{type(exc).__name__}: {exc}"[:1000], oracle_passed=False)
            state["heartbeat_at"] = now()
            write_json(receipt_path, state)
            if ready or running:
                time.sleep(5)
    state.update(status="complete" if all(r.get("oracle_passed") for r in state["rows"]) else "complete_with_unavailable_tasks",
                 finished_at=now())
    write_json(receipt_path, state)


if __name__ == "__main__":
    main()
