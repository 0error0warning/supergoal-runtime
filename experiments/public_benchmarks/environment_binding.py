"""Bind execution to an operator-selected task container, never its hostname.

Harbor 0.24's egress sidecar shares a network namespace with the main service.
Consequently /etc/hostname can identify that sidecar rather than the filesystem
where Harbor executes commands. The pinned Compose API is authoritative here.
"""
import json
import re


async def bind_task_container(environment, docker, *, main_service="main"):
    compose = getattr(environment, "_run_docker_compose_command", None)
    if callable(compose):
        result = await compose(["ps", "--quiet", main_service], timeout_sec=15)
        handles = (result.stdout or "").strip().splitlines()
        if result.return_code or len(handles) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", handles[0]):
            raise RuntimeError("Expected exactly one running Harbor main-service container")
        handle, method = handles[0], "harbor-0.24-compose-main-service"
    else:
        # Our OneDay operator owns this explicit handle. Do not guess names or
        # fall back to task-controlled hostname/cgroup output.
        handle = getattr(environment, "container", None)
        if not isinstance(handle, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", handle):
            raise RuntimeError("Environment has no authoritative Docker container handle")
        method = "operator-explicit-container"

    records = json.loads(await docker("inspect", handle))
    if len(records) != 1:
        raise RuntimeError("Container inspection was ambiguous")
    info = records[0]
    container = info["Id"]
    if not re.fullmatch(r"[0-9a-f]{64}", container) or not info["State"].get("Running"):
        raise RuntimeError("Selected task container is not running")
    if method.startswith("harbor-") and (info["Config"].get("Labels") or {}).get("com.docker.compose.service") != main_service:
        raise RuntimeError("Compose selected a container outside the main service")

    # Independent association check: both execution paths must see the same
    # mount namespace. This does not create task files or reveal hidden tests.
    probe = await environment.exec("readlink /proc/1/ns/mnt", timeout_sec=10, user="root")
    expected = (probe.stdout or "").strip()
    if probe.return_code or not re.fullmatch(r"mnt:\[\d+\]", expected):
        raise RuntimeError("Cannot verify the task mount namespace")
    observed = await docker("exec", "--user", "root", container, "readlink", "/proc/1/ns/mnt", timeout=10)
    if observed.strip() != expected:
        raise RuntimeError("Docker binding and Harbor task have different mount namespaces")
    return container, info, {"method": method, "mount_namespace": expected, "namespace_match": True}
