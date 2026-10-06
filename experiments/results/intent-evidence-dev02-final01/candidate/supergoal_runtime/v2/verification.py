"""Contract-supplied checks bound to immutable artifact digests.

Check commands must run inside the host's task sandbox. This module is not a
security sandbox. Tests are executable user/administrator contract policy;
model-generated commands must never be silently promoted into acceptance.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def manifest(root: Path, artifacts: list[str]) -> dict:
    root = root.resolve()
    values = {}
    for name in artifacts:
        path = (root / name).resolve()
        if not path.is_relative_to(root):
            raise ValueError("artifact escapes workspace")
        if path.is_file():
            with path.open("rb") as handle:
                values[name] = hashlib.file_digest(handle, "sha256").hexdigest()
        else:
            values[name] = None
    return values


def verify(root: Path, contract: dict) -> dict:
    artifacts = contract.get("artifacts", [])
    checks = contract.get("checks", [])
    if not artifacts or not checks:
        return {"verdict": "unknown", "reason": "explicit artifacts and acceptance checks required"}
    try:
        before = manifest(root, artifacts)
    except (OSError, ValueError) as exc:
        return {"verdict": "unknown", "reason": type(exc).__name__}
    results = []
    for check in checks:
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
            return {"verdict": "unknown", "reason": "invalid check argv"}
        try:
            result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                                    errors="replace", timeout=min(60, max(1, check.get("timeout", 20))))
            results.append({"id": check["id"], "exit_code": result.returncode,
                            "output": (result.stdout + result.stderr)[-5000:]})
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"verdict": "unknown", "reason": type(exc).__name__, "checks": results, "artifacts": before}
    try:
        after = manifest(root, artifacts)
    except (OSError, ValueError):
        after = {}
    passed = all(before.values()) and before == after and all(c["exit_code"] == 0 for c in results)
    return {"verdict": "pass" if passed else "fail", "checks": results,
            "artifacts": after, "stable": before == after,
            "reason": "configured checks passed" if passed else "missing, changed, or rejected artifacts"}
