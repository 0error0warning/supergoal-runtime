import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from experiments.public_benchmarks.environment_binding import bind_task_container


CID = "a" * 64


def fixture(*, service="main", namespace="mnt:[42]", explicit=False):
    info = {"Id": CID, "State": {"Running": True},
            "Config": {"Labels": {"com.docker.compose.service": service}}}
    docker = AsyncMock(side_effect=[json.dumps([info]), namespace])
    environment = SimpleNamespace(exec=AsyncMock(return_value=SimpleNamespace(
        stdout="mnt:[42]\n", return_code=0)))
    if explicit:
        environment.container = "operator-task"
    else:
        environment._run_docker_compose_command = AsyncMock(return_value=SimpleNamespace(
            stdout=CID + "\n", return_code=0))
    return environment, docker


def test_compose_main_is_bound_without_trusting_shared_hostname():
    environment, docker = fixture()
    container, _, binding = asyncio.run(bind_task_container(environment, docker))
    assert container == CID and binding["namespace_match"]
    environment._run_docker_compose_command.assert_awaited_once_with(
        ["ps", "--quiet", "main"], timeout_sec=15)
    assert all("hostname" not in str(call) for call in environment.exec.call_args_list)


def test_auxiliary_container_cannot_be_selected_as_main():
    environment, docker = fixture(service="harbor-docker-egress-control-sidecar")
    with pytest.raises(RuntimeError, match="outside the main service"):
        asyncio.run(bind_task_container(environment, docker))
    environment.exec.assert_not_awaited()


def test_multiple_main_replicas_are_rejected_instead_of_picking_first():
    environment, docker = fixture()
    environment._run_docker_compose_command.return_value.stdout = CID + "\n" + "b" * 64
    with pytest.raises(RuntimeError, match="exactly one"):
        asyncio.run(bind_task_container(environment, docker))
    docker.assert_not_awaited()


def test_mount_namespace_mismatch_prevents_execution():
    environment, docker = fixture(namespace="mnt:[99]")
    with pytest.raises(RuntimeError, match="different mount namespaces"):
        asyncio.run(bind_task_container(environment, docker))


def test_explicit_operator_handle_is_supported_and_verified():
    environment, docker = fixture(explicit=True)
    container, _, binding = asyncio.run(bind_task_container(environment, docker))
    assert container == CID
    assert binding["method"] == "operator-explicit-container"
    assert docker.call_args_list[0].args == ("inspect", "operator-task")


def test_missing_authoritative_handle_has_no_hostname_fallback():
    environment = SimpleNamespace(exec=AsyncMock())
    with pytest.raises(RuntimeError, match="no authoritative"):
        asyncio.run(bind_task_container(environment, AsyncMock()))
    environment.exec.assert_not_awaited()
