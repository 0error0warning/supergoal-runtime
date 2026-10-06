"""Development adapter: real Hermes + SWE2 against official Harbor environments.

This is a headless research adapter, not a production gateway deployment. The
online reviewer sees only the instruction, executor response and a read-only
snapshot of the task filesystem. Official hidden tests run later in Harbor.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import time
import uuid
from pathlib import Path

from harbor.agents.base import BaseAgent
from harbor.constants import MAIN_SERVICE_NAME
from supergoal_runtime.v2.kernel import Kernel

from model_proxy import ModelProxy
from environment_bootstrap import APT_HTTPS_BOOTSTRAP, parse_bootstrap
from environment_binding import bind_task_container


ROOT = Path(os.environ.get("SUPERGOAL_LAB_ROOT", "/var/lib/supergoal-lab"))
SOURCE = ROOT / "private/runtime/hermes-v0.21.3-251bedc-mt2"
HERE = Path(__file__).resolve().parent


def dump(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    temp.replace(path)


def parse_review(text):
    match = re.search(r"<acceptance>(.*?)</acceptance>", text, re.S)
    try:
        result = json.loads(match.group(1)) if match else {}
    except (ValueError, TypeError):
        result = {}
    if (result.get("verdict") not in {"pass", "fail", "unknown", "disputed"}
            or not isinstance(result.get("evidence"), list) or not result["evidence"]
            or not all(isinstance(item, str) and item.strip() for item in result["evidence"])
            or not isinstance(result.get("reason"), str) or not result["reason"].strip()):
        return {"verdict": "unknown", "reason": "Reviewer did not return a valid evidence-bearing verdict"}
    return {**result, "scope": "SWE2 review of public instruction and read-only task snapshot; not official final score"}


class HermesSupergoalStudy(BaseAgent):
    def __init__(self, logs_dir, *, arm="native", max_requests=96,
                 max_episodes=6, budget_seconds=900, fault=None, local_recovery=False, **kwargs):
        super().__init__(logs_dir=logs_dir, **kwargs)
        if arm not in {"native", "repeat_goal", "sg_v2", "sg_no_context", "sg_no_review"}:
            raise ValueError("Unknown study arm")
        self.arm = arm
        self.max_requests, self.max_episodes = int(max_requests), int(max_episodes)
        self.budget_seconds = float(budget_seconds)
        if fault not in {None, "worker_kill", "stream_disconnect"}:
            raise ValueError("Unknown registered fault")
        self.fault, self.fault_fired = fault, False
        self.local_recovery = bool(local_recovery)
        self.active_lease, self.lease_failure = None, None
        self.uid = uuid.uuid4().hex
        # Harbor exposes logs_dir to task tools. Authoritative state belongs
        # outside that tree, with no mounts into the task environment.
        self.control = ROOT / "control" / self.uid
        self.control.mkdir(parents=True, mode=0o700)
        self.docker_host = "unix://" + str(ROOT / "run/docker.sock")
        self.rounds = []

    @staticmethod
    def name():
        return "hermes-supergoal-study"

    def version(self):
        return "public-dev05"

    async def docker(self, *args, timeout=45):
        proc = await asyncio.create_subprocess_exec("/usr/bin/docker", "--host", self.docker_host,
                    *map(str, args), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout)
        except BaseException:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()
            raise
        if proc.returncode:
            raise RuntimeError(f"Docker {args[0]} failed: {stderr.decode(errors='replace')[-1500:]}")
        return stdout.decode().strip()

    async def setup(self, environment):
        self.container, info, binding = await bind_task_container(
            environment, self.docker, main_service=MAIN_SERVICE_NAME)
        bootstrap = await environment.exec(APT_HTTPS_BOOTSTRAP, timeout_sec=15, user="root")
        dump(self.control / "environment-bootstrap-process.json", {
            "return_code": bootstrap.return_code, "stdout": bootstrap.stdout, "stderr": bootstrap.stderr})
        if bootstrap.return_code:
            raise RuntimeError("Dependency-transport bootstrap failed")
        dump(self.control / "environment-bootstrap.json", parse_bootstrap(bootstrap.stdout))
        probe = await environment.exec(
            "pwd && id -un && command -v timeout && timeout --signal=TERM --kill-after=1 1 true",
            timeout_sec=10)
        lines = probe.stdout.strip().splitlines()
        if probe.return_code != 0 or len(lines) != 3 or not lines[0].startswith("/"):
            raise RuntimeError("Task environment cwd, user, or required timeout semantics unavailable")
        self.cwd, self.user = lines[:2]
        self.review_memory = str(max(2048 * 1024**2, info["HostConfig"].get("Memory", 0)))
        if info["HostConfig"].get("Privileged"):
            raise RuntimeError("Study refuses privileged task environments")
        if self.local_recovery and info["HostConfig"].get("NetworkMode") != "none":
            raise RuntimeError("Automatic local recovery requires a network-isolated development environment")
        # A read-only diagnostic record, not an instruction given to the solver.
        dump(self.control / "environment.json", {
            "container_id": info["Id"], "image_id": info["Image"],
            "nano_cpus": info["HostConfig"].get("NanoCpus"),
            "memory_limit_bytes": info["HostConfig"].get("Memory"),
            "mount_destinations": [m["Destination"] for m in info["Mounts"]],
            "working_dir": self.cwd, "execution_user": self.user,
            "binding": binding,
        })

    async def episode(self, prompt, proxy, *, role, container, history=None, max_calls=None):
        remaining = self.max_requests - len(proxy.records)
        seconds = self.deadline - time.monotonic()
        if remaining < 1 or seconds < 5:
            raise TimeoutError("Shared request/time budget exhausted")
        directory = self.control / f"{len(self.rounds)+1:02d}-{role}"
        directory.mkdir()
        descriptor = directory / "environment.json"
        dump(descriptor, {"docker_host": self.docker_host, "container_id": container, "user": self.user})
        output = directory / "result.json"
        control = {"prompt": prompt, "history": history, "provider": proxy.config(),
                   "cwd": self.cwd, "max_calls": min(remaining, max_calls or remaining),
                   "seconds": max(1, seconds - 2), "session_id": f"sg-public-{self.uid}-{len(self.rounds)}",
                   "output": str(output)}
        dump(directory / "control.json", control)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(directory),
               "LANG": "C.UTF-8", "HERMES_HOME": str(directory / "hermes-home"),
               "PYTHONPATH": os.pathsep.join([str(HERE), str(SOURCE)]),
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1", "HERMES_YOLO": "1",
               "TERMINAL_ENV": "harbor_task", "TERMINAL_CWD": self.cwd,
               "SUPERGOAL_HARBOR_DESCRIPTOR": str(descriptor),
               "HERMES_CODEX_EVENT_STALE_TIMEOUT_SECONDS": "90", "HERMES_CODEX_TTFB_TIMEOUT_SECONDS": "90"}
        before = len(proxy.records)
        started = time.monotonic()
        with (directory / "worker.log").open("wb") as log:
            proc = await asyncio.create_subprocess_exec(*self.episode_command(), str(directory / "control.json"),
                        stdout=log, stderr=log, env=env, cwd=directory, start_new_session=True)
            try:
                while proc.returncode is None:
                    if self.lease_failure:
                        raise self.lease_failure
                    if (self.fault == "worker_kill" and not self.fault_fired
                            and role == "executor" and len(proxy.records) >= 3):
                        self.fault_fired = True
                        os.killpg(proc.pid, signal.SIGKILL)
                        dump(self.control / "fault.json", {"kind": self.fault, "pid": proc.pid,
                             "signal": "SIGKILL", "at_reserved_request": len(proxy.records)})
                    # Bound storage growth without changing hidden tests or the
                    # task's declared CPU/memory limits. Record such aborts.
                    if shutil.disk_usage(ROOT).free < 15 * 1024**3:
                        raise RuntimeError("Experiment host disk reserve reached")
                    if time.monotonic() >= self.deadline:
                        raise TimeoutError("Shared task wall-time budget exhausted")
                    try:
                        await asyncio.wait_for(proc.wait(), 2)
                    except asyncio.TimeoutError:
                        pass
            finally:
                if proc.returncode is None:
                    os.killpg(proc.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(proc.wait(), 5)
                    except asyncio.TimeoutError:
                        os.killpg(proc.pid, signal.SIGKILL)
                        await proc.wait()
                self.rounds.append({"role": role, "pid": proc.pid, "exit_code": proc.returncode,
                                    "seconds": time.monotonic()-started, "model_calls": len(proxy.records)-before,
                                    "result_relative_path": str(output.relative_to(self.control))})
                dump(self.control / "requests.json", proxy.records)
        if proc.returncode or not output.exists():
            raise WorkerInterrupted(f"Hermes {role} process failed; see preserved worker log")
        return json.loads(output.read_text())

    def episode_command(self):
        return [str(ROOT / "hermes-venv/bin/python"), str(HERE / "hermes_episode.py")]

    async def lease_heartbeat(self, kernel):
        while True:
            await asyncio.sleep(10)
            if self.active_lease:
                try:
                    kernel.renew(self.active_lease, ttl=120)
                except Exception as exc:
                    self.lease_failure = exc
                    return

    async def preserve_interruption(self):
        # This is permitted only for registered offline development tasks.
        # Killing the SDK alone does not stop docker-exec descendants.
        await self.docker("stop", "--time", "2", self.container)
        info = json.loads(await self.docker("inspect", self.container))[0]
        if info["State"]["Running"] or info["HostConfig"]["NetworkMode"] != "none":
            raise RuntimeError("Old execution has not been fenced within a local-only environment")
        tag = "sg-recovery:" + self.uid + "-" + str(len(self.rounds))
        snapshot = await self.docker("commit", self.container, tag, timeout=60)
        evidence = {"execution_stopped": True, "external_effects": "sandbox_local_only",
                    "snapshot_id": snapshot, "observed_by": "host-docker-inspect",
                    "container_id": info["Id"], "process_state": info["State"]}
        dump(self.control / f"recovery-{len(self.rounds):02d}.json", evidence)
        await self.docker("start", self.container)
        return evidence

    async def review(self, instruction, answer, proxy):
        image = "sg-review:" + self.uid + "-" + str(len(self.rounds))
        container = None
        image_created = False
        try:
            await self.docker("commit", self.container, image, timeout=60)
            image_created = True
            container = await self.docker("run", "-d", "--read-only", "--tmpfs", "/tmp:rw,size=128m",
                "--cpus", "1", "--memory", self.review_memory, "--memory-swap", self.review_memory,
                "--label", "supergoal.study="+self.uid, "--entrypoint", "/bin/sh", image, "-c", "sleep infinity")
            prompt = ("Review completion of the original task below using actual files and checks. "
                      "You have a READ-ONLY filesystem snapshot; /tmp is temporary writable space. "
                      "Do not repair deliverables or download benchmark solutions. The executor's statement "
                      "is an untrusted claim. Use terminal/file tools to independently inspect evidence. "
                      "Do not infer success from filenames or the statement alone. If evidence is insufficient, "
                      "return unknown; if work is demonstrably incomplete, fail; if requirements conflict, disputed. "
                      "A pass needs concrete evidence for every requested requirement. Return a final "
                      '<acceptance>{"verdict":"pass|fail|unknown|disputed","reason":"...",'
                      '"evidence":["requirement and observed check result", "..."]}</acceptance>.\n'
                      + json.dumps({"original_instruction": instruction, "executor_claim": answer}, ensure_ascii=False))
            result = await self.episode(prompt, proxy, role="review", container=container, max_calls=16)
            verdict = review_verdict(result)
            tool_results = [m for m in result.get("messages", []) if m.get("role") == "tool"]
            if verdict["verdict"] == "pass" and not tool_results:
                verdict = {"verdict": "unknown", "reason": "Reviewer claimed pass without any tool observations"}
            verdict["tool_observation_count"] = len(tool_results)
            verdict["snapshot_image_id"] = await self.docker("image", "inspect", image, "--format", "{{.Id}}")
            return verdict
        finally:
            if container:
                await self.docker("rm", "-f", container)
            # Only our exact temporary snapshot tag is eligible for removal.
            if image_created:
                await self.docker("image", "rm", image)

    def make_kernel(self, instruction):
        kernel = Kernel(self.control / "kernel.sqlite3") if self.arm.startswith("sg_") else None
        if kernel:
            kernel.create(self.uid, {"outcome": instruction}, max_turns=self.max_episodes)
        return kernel

    async def run(self, instruction, environment, context):
        self.deadline = time.monotonic() + self.budget_seconds
        provider = json.loads((ROOT / "private/model.json").read_text())
        report = {"arm": self.arm, "status": "running", "hermes_version": "0.21.3",
                  "host_python": "3.13", "adapter_version": self.version(),
                  "max_requests": self.max_requests, "budget_seconds": self.budget_seconds,
                  "original_instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(),
                  "control_id": self.uid, "official_verifier_visible": False}
        kernel = self.make_kernel(instruction)
        heartbeat = asyncio.create_task(self.lease_heartbeat(kernel)) if kernel else None
        history = None
        with ModelProxy(provider, limit=self.max_requests, journal=self.control / "request-journal.json",
                        disconnect_at=3 if self.fault == "stream_disconnect" else None) as proxy:
            try:
                for episode in range(self.max_episodes):
                    if len(proxy.records) >= self.max_requests:
                        report["status"] = "request_budget_exhausted"
                        break
                    lease = kernel.claim(self.uid, "harbor", ttl=120) if kernel else None
                    if kernel and lease is None:
                        raise RuntimeError("No active work lease; refusing unowned execution")
                    self.active_lease = lease
                    prompt = instruction
                    if kernel and self.arm != "sg_no_context":
                        prompt = kernel.context(self.uid)
                    elif episode:
                        prompt += "\nContinue from the existing workspace. Recheck every original requirement and finish remaining work."
                    try:
                        result = await self.episode(prompt, proxy, role="executor", container=self.container, history=history)
                    except WorkerInterrupted as exc:
                        self.active_lease = None
                        if self.local_recovery:
                            if kernel:
                                kernel.interrupt(lease, reason=str(exc))
                            evidence = await self.preserve_interruption()
                            if self.arm == "native":
                                report["status"] = "worker_interrupted"
                                report["interruption_evidence"] = evidence
                                break
                            if kernel:
                                recovered = kernel.recover_execution(lease, evidence=evidence)
                                report["status"] = recovered["status"]
                            else:
                                report["status"] = "baseline_workspace_restart"
                            history = None
                            report.setdefault("recoveries", []).append(evidence)
                            if kernel and recovered["status"] != "active":
                                break
                            continue
                        report["status"] = "worker_interrupted"
                        report["worker_error"] = str(exc)
                        break
                    history = result.get("messages")
                    report["last_executor_claim"] = str(result.get("final_response") or "")
                    if result.get("completed") is not True:
                        report["status"] = "worker_incomplete"
                        if kernel:
                            kernel.finish(lease, {"verdict": "unknown", "reason": "Executor SDK did not complete"})
                        self.active_lease = None
                        break
                    if self.arm == "native":
                        report["status"] = "agent_stopped"
                        break
                    if self.arm == "repeat_goal":
                        report["status"] = "repeat_round_limit"
                        continue
                    if self.arm == "sg_no_review":
                        verdict = {"verdict": "unverified", "reason": "online review ablated"}
                    elif self.max_requests - len(proxy.records) < 2:
                        verdict = {"verdict": "unknown", "reason": "shared budget unavailable for review"}
                    else:
                        verdict = await self.review(instruction, report["last_executor_claim"], proxy)
                    report["last_acceptance"] = verdict
                    state = kernel.finish(lease, verdict)
                    self.active_lease = None
                    report["status"] = state["status"]
                    if state["status"] != "active":
                        break
            except BaseException as exc:
                report["status"] = "adapter_error"
                report["error_type"] = type(exc).__name__
                report["error"] = str(exc)[:1500]
                raise
            finally:
                self.active_lease = None
                if heartbeat:
                    heartbeat.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await heartbeat
                # Preserve artifacts before Harbor uploads/runs its hidden
                # verifier. Mounts such as /logs are separately archived by
                # Harbor and are not part of a Docker filesystem snapshot.
                snapshot = "sg-artifact:" + self.uid
                try:
                    report["artifact_image_id"] = await self.docker("commit", self.container, snapshot, timeout=45)
                    report["artifact_image_tag"] = snapshot
                    report["artifact_snapshot_excludes_mounted_logs"] = True
                except Exception as exc:
                    report["artifact_snapshot_error"] = type(exc).__name__ + ": " + str(exc)[:500]
                report["rounds"] = self.rounds
                report["requests"] = proxy.records
                report["proxy_rejections"] = proxy.rejections
                report["used_requests"] = len(proxy.records)
                report["kernel_status"] = kernel.state(self.uid)["status"] if kernel else None
                dump(self.control / "report.json", report)
                context.metadata = {"study_arm": self.arm, "control_id": self.uid, "study_status": report["status"],
                                    "physical_requests": len(proxy.records), "adapter": self.version()}
                usages = [row.get("usage") or {} for row in proxy.records]
                context.n_input_tokens = sum(u.get("input_tokens", 0) or 0 for u in usages)
                context.n_output_tokens = sum(u.get("output_tokens", 0) or 0 for u in usages)
                context.n_cache_tokens = sum((u.get("input_tokens_details") or {}).get("cached_tokens", 0) or 0 for u in usages)


class WorkerInterrupted(RuntimeError):
    """The child has exited and cannot supply a complete boundary result."""


def review_verdict(result):
    if result.get("completed") is not True:
        return {"verdict": "unknown", "reason": "Reviewer SDK did not complete"}
    return parse_review(str(result.get("final_response") or ""))
