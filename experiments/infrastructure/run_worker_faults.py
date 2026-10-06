"""Real SWE2/Hermes worker fault development study, isolated from public tasks."""
from __future__ import annotations

import asyncio
import json
import shlex
from types import SimpleNamespace

from harbor_agent import HermesSupergoalStudy, ROOT, dump


INSTRUCTION = """Complete the finite data-processing task in /workspace. Inspect orders.csv and rates.json.
Create pipeline.py using Python's standard library. Ignore cancelled orders and convert EUR to USD
using rates.json. Round each order to integer cents before summing. Produce totals.json mapping
each customer to integer USD cents, and audit.csv listing every input order with included/excluded
status and converted cents (zero for cancelled orders). Produce report.md describing totals,
exclusions and the rounding rule. The program must regenerate both outputs from the original inputs
without modifying them. Run it, then independently check the JSON and CSV against the input rows.
Read the existing workspace before changing anything; it may contain partially completed work.
Do not use network services. Stop when all requested files and checks are complete."""
ORDERS = "order_id,customer,currency,amount,status\nA,Ada,USD,12.50,paid\nB,Ada,EUR,10.00,paid\nC,Lin,USD,99.00,cancelled\nD,Lin,EUR,2.50,paid\nE,Ada,USD,0.05,paid\n"


class OfflineEnvironment:
    def __init__(self, agent, container):
        self.agent, self.container = agent, container

    async def exec(self, command, timeout_sec=30, user="root"):
        output = await self.agent.docker("exec", "--user", user, self.container,
                                        "bash", "-c", command, timeout=timeout_sec + 5)
        return SimpleNamespace(stdout=output, stderr="", return_code=0)


async def main():
    target = ROOT / "setup/worker-fault-dev05.json"
    if target.exists():
        raise FileExistsError(target)
    image = json.loads((ROOT / "setup/oneday-assets01.json").read_text())["image_id"]
    state = {"status": "running", "scope": "SDK worker death and proxy stream disconnection; NOT whole-controller reboot",
             "model": "devin/swe-2", "rows": [], "fault_request_ordinal": 3,
             "synthetic_development_task": True, "public_evaluation_tasks_used": False}
    dump(target, state)
    plan = [(None, "sg_v2")] + [(fault, arm) for fault in ["worker_kill", "stream_disconnect"]
                                  for arm in ["native", "repeat_goal", "sg_v2"]]
    for fault, arm in plan:
        agent = HermesSupergoalStudy(ROOT / "control", arm=arm, max_requests=48,
                                    max_episodes=4, budget_seconds=600, fault=fault, local_recovery=True)
        container = await agent.docker("run", "-d", "--network", "none", "--cpus", "1",
                                      "--memory", "2g", "--memory-swap", "2g", "--pids-limit", "256",
                                      "--workdir", "/workspace", "--entrypoint", "/bin/sh", image, "-c", "sleep infinity")
        row = {"arm": arm, "fault": fault, "control_id": agent.uid, "status": "running"}
        state["rows"].append(row)
        dump(target, state)
        try:
            command = ("mkdir -p /workspace; printf %s " + shlex.quote(ORDERS) + " > /workspace/orders.csv; "
                       "printf '%s' '{\"EUR\":1.2,\"USD\":1.0}' > /workspace/rates.json")
            await agent.docker("exec", container, "sh", "-c", command)
            environment = OfflineEnvironment(agent, container)
            await agent.setup(environment)
            context = SimpleNamespace()
            await agent.run(INSTRUCTION, environment, context)
            # This deterministic final check is available only to the operator.
            check = "import json,csv,pathlib; p=pathlib.Path('/workspace'); d=json.loads((p/'totals.json').read_text()); a=list(csv.DictReader((p/'audit.csv').open())); assert d=={'Ada':2455,'Lin':300},d; assert len(a)==5; assert (p/'pipeline.py').stat().st_size>0; assert (p/'report.md').stat().st_size>0; print('artifact_check_passed')"
            row["deterministic_artifact_check"] = await agent.docker("exec", container, "python", "-c", check)
            row["artifact_passed"] = True
        except Exception as exc:
            row.update(error=f"{type(exc).__name__}: {exc}"[:1200], artifact_passed=False)
        finally:
            report = agent.control / "report.json"
            if report.exists():
                data = json.loads(report.read_text())
                row.update(study_status=data["status"], requests=data["used_requests"],
                           recoveries=len(data.get("recoveries", [])),
                           fault_triggered=agent.fault_fired or any(r.get("injected_fault") for r in data["requests"]))
            row["status"] = "finished"
            dump(target, state)
            await agent.docker("rm", "-f", container)
    state["status"] = "complete"
    dump(target, state)


if __name__ == "__main__":
    asyncio.run(main())
