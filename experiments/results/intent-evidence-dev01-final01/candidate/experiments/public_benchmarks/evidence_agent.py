"""Opt-in intent/evidence policy study using the real isolated Hermes executor.

All model roles share the existing physical request journal and wall deadline.
The hidden scorer is not visible to the interpreter, executor, or online audit.
"""
from contextlib import asynccontextmanager
import json

from checkpoint_agent import SnapshotMixin
from harbor_agent import HermesSupergoalStudy, dump
from supergoal_runtime.v2.kernel import Kernel
from supergoal_runtime.v2.work_policy import (
    assess_coverage, discovery_prompt, execution_prompt, fallback_brief,
    parse_brief, review_prompt,
)


class EvidenceStudy(SnapshotMixin, HermesSupergoalStudy):
    def __init__(self, *args, arm="evidence", **kwargs):
        if arm not in {"native", "repeat_goal", "sg_v2", "evidence", "coverage_only"}:
            raise ValueError("unregistered evidence study arm")
        self.study_arm = arm
        self.brief = None
        self.audits = []
        self.snapshots = 0
        super().__init__(*args, arm="sg_v2" if arm in {"evidence", "coverage_only"} else arm, **kwargs)

    def version(self):
        return "intent-evidence-dev01"

    def make_kernel(self, instruction):
        self.original_instruction = instruction
        if self.study_arm not in {"evidence", "coverage_only"}:
            return super().make_kernel(instruction)
        kernel = Kernel(self.control / "kernel.sqlite3")
        kernel.create(self.uid, {"outcome": instruction, "work_policy": "evidence-v1",
                                "verification_unknown": "continue"}, max_turns=self.max_episodes)
        return kernel

    @asynccontextmanager
    async def observation_snapshot(self):
        self.snapshots += 1
        image = f"sg-observe:{self.uid}-{self.snapshots}"
        container, created = None, False
        await self.docker("pause", self.container)
        try:
            await self.docker("commit", self.container, image, timeout=60)
            created = True
            image_id = await self.docker("image", "inspect", image, "--format", "{{.Id}}")
            container = await self.docker("run", "-d", "--read-only", "--tmpfs", "/tmp:rw,size=512m",
                "--cpus", "1", "--memory", self.review_memory, "--memory-swap", self.review_memory,
                "--label", "supergoal.study=" + self.uid, "--entrypoint", "/bin/sh", image,
                "-c", "sleep infinity")
            yield container, image_id
        finally:
            # Unpause even when snapshot creation or child cleanup fails.
            try:
                if container:
                    await self.docker("rm", "-f", container)
                if created:
                    await self.docker("image", "rm", image)
            finally:
                await self.docker("unpause", self.container)

    async def episode(self, prompt, proxy, *, role, container, history=None, max_calls=None):
        if role == "executor" and self.study_arm in {"evidence", "coverage_only"}:
            if self.brief is None:
                if self.study_arm == "evidence":
                    async with self.observation_snapshot() as (snapshot, image_id):
                        result = await super().episode(discovery_prompt(self.original_instruction), proxy,
                            role="interpret", container=snapshot, max_calls=8)
                    try:
                        if result.get("completed") is not True:
                            raise ValueError("interpreter did not complete")
                        self.brief = parse_brief(result.get("final_response") or "", self.original_instruction)
                    except (ValueError, TypeError):
                        self.brief = fallback_brief(self.original_instruction, "invalid interpreter result")
                    self.brief["initial_snapshot_id"] = image_id
                else:
                    self.brief = fallback_brief(self.original_instruction, "interpretation ablated")
                dump(self.control / "goal-brief.json", self.brief)
            if self.study_arm == "evidence":
                prompt = execution_prompt(prompt, self.brief)
        return await super().episode(prompt, proxy, role=role, container=container,
                                     history=history, max_calls=max_calls)

    async def review(self, instruction, answer, proxy):
        if self.study_arm not in {"evidence", "coverage_only"}:
            await self.docker("pause", self.container)
            try:
                return await super().review(instruction, answer, proxy)
            finally:
                await self.docker("unpause", self.container)
        async with self.observation_snapshot() as (snapshot, image_id):
            result = await super().episode(review_prompt(self.brief, answer), proxy,
                                            role="review", container=snapshot, max_calls=20)
            if result.get("completed") is True:
                verdict = assess_coverage(result.get("final_response") or "", self.brief,
                                          result.get("messages") or [], artifact_id=image_id)
            else:
                verdict = {"verdict": "unknown", "reason": "Auditor SDK did not complete", "artifact_id": image_id}
        self.audits.append(verdict)
        dump(self.control / "coverage-audits.json", self.audits)
        return verdict

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            path = self.control / "report.json"
            if path.exists():
                report = json.loads(path.read_text())
                report.update(arm=self.study_arm, goal_brief=self.brief, coverage_audits=self.audits,
                              policy="evidence-v1" if self.study_arm == "evidence" else None)
                dump(path, report)
            if getattr(context, "metadata", None):
                context.metadata["study_arm"] = self.study_arm
