"""Read-only Linux host inventory; never starts Docker or downloads VM images.

This checks capacity and installed tools, not benchmark readiness. Dataset
permissions, pinned assets, GUI/model compatibility and oracle runs are separate.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

GIB = 1024 ** 3


def inspect_host(root: Path) -> dict:
    result = {"captured_at": time.time(), "platform": platform.system(),
              "machine": platform.machine(), "cpu_count": os.cpu_count(),
              "probe_only": True, "benchmark_ready": False}
    if platform.system() != "Linux":
        return {**result, "reason": "Run this on the independent Linux experiment server; no local Docker inspection performed."}
    target = root.expanduser().resolve()
    while not target.exists():
        target = target.parent
    memory = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value = line.split(":", 1)
        if key in {"MemTotal", "MemAvailable"}:
            memory[key] = int(value.strip().split()[0]) * 1024
    result.update(memory_bytes=memory, disk_free_bytes=shutil.disk_usage(target).free,
                  inspected_filesystem=str(target), uid=os.geteuid())
    kvm = {"exists": Path("/dev/kvm").exists(), "usable": False}
    try:
        import fcntl
        fd = os.open("/dev/kvm", os.O_RDWR | os.O_CLOEXEC)
        try:
            kvm["api_version"] = fcntl.ioctl(fd, 0xAE00, 0)  # KVM_GET_API_VERSION; no VM created
            kvm["usable"] = kvm["api_version"] == 12
        finally:
            os.close(fd)
    except OSError as exc:
        kvm["error"] = type(exc).__name__
    result["kvm"] = kvm
    result["tools"] = {name: shutil.which(name) for name in
                       ["docker", "python3", "node", "npm", "qemu-img", "tmux", "libreoffice", "ffmpeg", "pdftoppm"]}
    if result["tools"]["docker"]:
        try:
            probe = subprocess.run(["docker", "info", "--format", "{{json .ServerVersion}}"],
                                   capture_output=True, text=True, timeout=10)
            result["docker"] = {"usable": probe.returncode == 0 and probe.stdout.strip() not in {"", '""', "null"},
                                "server_version": probe.stdout.strip() if probe.returncode == 0 else None}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result["docker"] = {"usable": False, "error": type(exc).__name__}
    # Planning allowances, not universal benchmark requirements. Actual image
    # sizes, build peaks and existing services must be checked before admitting
    # a task. KVM applies only to the desktop VM suites.
    result["capacity_profiles"] = {
        "serial_2GiB_cli_planning": {
            "concurrency": 1,
            "task_cpus_up_to": 1,
            "task_memory_bytes_up_to": 2 * GIB,
            "controller_memory_allowance_bytes": GIB,
            "host_available_memory_reserve_bytes": int(1.5 * GIB),
            "available_memory_meets_allowance": memory.get("MemAvailable", 0) >= 4.5 * GIB,
            "kvm_required": False,
            "benchmark_ready": False,
            "note": "Check actual memory again before each task and measure controller/container peaks.",
        },
        "serial_cli_planning": {
            "concurrency": 1,
            "task_cpus_up_to": 2,
            "task_memory_bytes_up_to": 4 * GIB,
            "task_storage_bytes_up_to": 10 * GIB,
            "host_memory_reserve_bytes": 4 * GIB,
            "image_and_build_allowance_bytes": 12 * GIB,
            "archive_allowance_bytes": 3 * GIB,
            "disk_reserve_bytes": 5 * GIB,
            "cpu_allowance_available": (os.cpu_count() or 0) >= 4,
            "available_memory_meets_allowance": memory.get("MemAvailable", 0) >= 8 * GIB,
            "disk_meets_provisional_30GiB_allowance": result["disk_free_bytes"] >= 30 * GIB,
            "kvm_required": False,
            "benchmark_ready": False,
            "note": "A planning profile only. Smaller measured task environments may fit below the provisional disk allowance; larger images/builds may exceed it. Check the Docker data filesystem separately.",
        },
        "weavebench_documented_vm_capacity": {
            "total_memory_at_least_32GB": memory.get("MemTotal", 0) >= 32_000_000_000,
            "recommended_150GB_free": result["disk_free_bytes"] >= 150_000_000_000,
            "kvm_access_for_this_user": kvm["usable"],
            "applies_to": "LongHorizon WeaveBench VM environment only; not a requirement for the CLI study",
        },
    }
    result["remaining_checks"] = ["task-specific resources and image/build peak", "Docker data filesystem capacity",
                                  "dataset access", "release/asset/image consistency", "isolated oracle preflight",
                                  "sandbox control/data separation", "SWE2 image input only for GUI tasks"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/srv/supergoal-lab"))
    args = parser.parse_args()
    print(json.dumps(inspect_host(args.root), indent=2, ensure_ascii=False))
