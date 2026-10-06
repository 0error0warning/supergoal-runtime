"""Factorial context interventions; the public-dev05 control remains frozen."""
from __future__ import annotations

import json

from harbor_agent import HermesSupergoalStudy, dump


CONDITIONS = {
    "native": ("native", False, False),
    "sg_v2": ("sg_v2", False, False),
    "sg_no_context": ("sg_no_context", False, False),
    "sg_fresh": ("sg_v2", True, False),
    "sg_fresh_no_context": ("sg_no_context", True, False),
    "sg_retry_unknown": ("sg_v2", False, True),
}


class MechanismStudy(HermesSupergoalStudy):
    def __init__(self, *args, arm="sg_v2", **kwargs):
        base_arm, self.fresh_context, self.retry_unknown = CONDITIONS[arm]
        self.condition = arm
        self.review_attempts = []
        super().__init__(*args, arm=base_arm, **kwargs)

    def version(self):
        return "mechanism-dev06"

    async def episode(self, prompt, proxy, *, role, container, history=None, max_calls=None):
        if self.fresh_context and role == "executor":
            history = None
        return await super().episode(prompt, proxy, role=role, container=container,
                                     history=history, max_calls=max_calls)

    async def review(self, instruction, answer, proxy):
        # Freeze executor processes for the complete audit interval. This also
        # makes executor/reviewer CPU phases mutually exclusive: a 1-CPU trial
        # need not reserve two CPUs while waiting on the model. Filesystem
        # snapshot limitations (no running services or mounts) still apply.
        await self.docker("pause", self.container)
        try:
            return await self._paused_review(instruction, answer, proxy)
        finally:
            await self.docker("unpause", self.container)

    async def _paused_review(self, instruction, answer, proxy):
        first = await super().review(instruction, answer, proxy)
        self.review_attempts.append(first)
        # One additional independent audit of the unchanged executor workspace.
        # A failure or dispute never receives a retry, and a second unknown
        # remains unknown. Both attempts are charged to the same physical budget.
        if (self.retry_unknown and first["verdict"] == "unknown"
                and self.max_requests - len(proxy.records) >= 2):
            second = await super().review(instruction, answer, proxy)
            self.review_attempts.append(second)
            return {**second, "prior_unknown": first, "bounded_reaudit": True}
        return first

    async def run(self, instruction, environment, context):
        try:
            await super().run(instruction, environment, context)
        finally:
            path = self.control / "report.json"
            if path.exists():
                report = json.loads(path.read_text())
                report.update(arm=self.condition, context_history="fresh" if self.fresh_context else "retained",
                              review_attempts=self.review_attempts,
                              executor_paused_during_audit=self.arm.startswith('sg_'),
                              sqlite_version=__import__("sqlite3").sqlite_version)
                dump(path, report)
            if getattr(context, "metadata", None):
                context.metadata["study_arm"] = self.condition
