"""Cross-cohort admission accounting; never reclaims a crashed owner's work."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path


def fits(allocations, request, *, cpus=6, memory_mb=49152, workers=6):
    if request["cpus"] <= 0 or request["memory_mb"] <= 0:
        raise ValueError("positive resource reservation required")
    return (len(allocations) < workers
            and sum(a["cpus"] for a in allocations) + request["cpus"] <= cpus
            and sum(a["memory_mb"] for a in allocations) + request["memory_mb"] <= memory_mb)


def reservation(task, arm):
    review = arm in {"sg_v2", "sg_no_context"}
    memory = task["resources"]["memory_mb"]
    return {"cpus": task["resources"]["cpus"] + int(review),
            "memory_mb": memory + (max(2048, memory) if review else 0)}


class ResourcePool:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def transaction(self):
        import fcntl

        with self.path.with_suffix(".lock").open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield state
            temp = self.path.with_suffix(".tmp")
            with temp.open("w") as output:
                json.dump(state, output, indent=2)
                output.flush()
                os.fsync(output.fileno())
            temp.replace(self.path)

    def acquire(self, job, request):
        with self.transaction() as state:
            if job in state:
                raise ValueError("Job already owns resources; audit instead of duplicating it")
            if not fits(list(state.values()), request):
                return False
            state[job] = {**request, "owner_pid": os.getpid()}
            return True

    def release(self, job):
        with self.transaction() as state:
            if state[job]["owner_pid"] != os.getpid():
                raise ValueError("Cannot release another operator's reservation")
            del state[job]
