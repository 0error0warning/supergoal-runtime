"""Hermes terminal provider attached to one existing Harbor task container.

The model's terminal and file tools run inside that container. The controller,
credentials, kernel and hidden tests are never mounted by this provider.
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from agent.terminal_env_provider import TerminalEnvironmentProvider
from tools.environments.base import BaseEnvironment
from tools.environments.base_output import _popen_bash


class HarborTaskEnvironment(BaseEnvironment):
    def __init__(self, *, cwd, timeout, descriptor):
        self.descriptor = descriptor
        if not re.fullmatch(r"[0-9a-f]{12,64}", descriptor["container_id"]):
            raise ValueError("Expected a Docker container ID")
        super().__init__(cwd=cwd, timeout=timeout)
        # Match the pinned Hermes Docker backend's lifecycle. Without this,
        # every command uses a login shell and exported state is not retained.
        # In the observed images, login-shell teardown can also turn a completed
        # atomic write (set -e) into an erroneous nonzero tool result.
        self.init_session()

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        # timeout executes INSIDE the container: killing a docker client alone
        # does not stop its remote command. Harbor owns final container cleanup.
        command = ["/usr/bin/docker", "--host", self.descriptor["docker_host"],
                   "exec", "-i", "--user", str(self.descriptor["user"]),
                   "--env", "PYTHONDONTWRITEBYTECODE=1", self.descriptor["container_id"],
                   "timeout", "--signal=TERM", "--kill-after=3s", str(max(1, int(timeout))),
                   "bash", "-lc" if login else "-c", cmd_string]
        return _popen_bash(command, stdin_data)

    def cleanup(self):
        # This backend never owns the container's lifecycle or host mounts.
        pass


class HarborTaskProvider(TerminalEnvironmentProvider):
    name = "harbor_task"
    is_remote = True
    is_container = True
    session_isolated_when_nonpersistent = True

    @property
    def cache_path_base(self):
        return "/tmp/hermes-cache"

    def is_available(self):
        return Path(os.environ.get("SUPERGOAL_HARBOR_DESCRIPTOR", "/nonexistent")).is_file()

    def create_environment(self, *, cwd, timeout, **kwargs):
        descriptor = json.loads(Path(os.environ["SUPERGOAL_HARBOR_DESCRIPTOR"]).read_text())
        return HarborTaskEnvironment(cwd=cwd, timeout=timeout, descriptor=descriptor)


def register(ctx):
    ctx.register_terminal_environment_provider(HarborTaskProvider())


def smoke_commands(marker):
    """Commands used by the operator's no-model tool preflight."""
    return ["pwd", "cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/cpu.max",
            "test ! -e /var/lib/supergoal-lab/private/model.json",
            "test ! -S /var/lib/supergoal-lab/run/docker.sock",
            "printf bridge_ok > " + shlex.quote(marker)]
