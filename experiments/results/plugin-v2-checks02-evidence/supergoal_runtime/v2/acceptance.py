"""Versioned acceptance policies with calibration and explicit uncertainty.

Policies are trusted operator input, never generated implicitly from task text.
Commands execute with host privileges: callers must supply an isolated runner
for untrusted workloads. Pins and calibration detect mistakes/drift; they are
not an OS security boundary or a proof of complete semantic coverage.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

from .kernel import encode
from .verification import manifest


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def tree_digest(root: Path) -> str:
    entries = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("calibration fixtures must not contain symlinks")
        if path.is_file():
            entries[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest(entries)


def load_policy(path: str | Path) -> dict:
    """Freeze paths, source code and calibration fixtures from an operator file."""
    source = Path(path).expanduser().resolve(strict=True)
    policy = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(policy, dict) or policy.get("schema") != 1:
        raise ValueError("acceptance policy schema must be 1")
    for key in ("outcome", "scope", "workspace"):
        if not isinstance(policy.get(key), str) or not policy[key].strip():
            raise ValueError(f"explicit {key} required")
    workspace = (source.parent / policy["workspace"]).resolve(strict=True)
    if not workspace.is_dir() or source.is_relative_to(workspace):
        raise ValueError("policy must be outside its task workspace")
    policy["workspace"] = str(workspace)
    criteria = policy.get("criteria")
    if (not isinstance(criteria, dict) or not criteria
            or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in criteria.items())):
        raise ValueError("criteria must map stable identifiers to explicit requirements")
    artifacts = policy.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or not all(isinstance(x, str) and x for x in artifacts):
        raise ValueError("explicit artifact paths required")
    manifest(workspace, artifacts)  # validates containment; outputs may not yet exist
    checks = policy.get("checks")
    if not isinstance(checks, list) or not checks:
        raise ValueError("at least one explicit check required")
    ids, coverage, total_timeout = set(), set(), 0.0
    for check in checks:
        if (not isinstance(check, dict) or not isinstance(check.get("id"), str)
                or not check["id"] or check["id"] in ids):
            raise ValueError("check identifiers must be unique")
        ids.add(check["id"])
        covers = check.get("covers")
        if not isinstance(covers, list) or not covers or any(c not in criteria for c in covers):
            raise ValueError("every check must name the criteria it evaluates")
        coverage.update(covers)
        argv = check.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(x, str) and x for x in argv):
            raise ValueError("checks require an explicit argv list")
        timeout = float(check.get("timeout", 5))
        if not 0 < timeout <= 15:
            raise ValueError("check timeouts must be within (0, 15] seconds")
        check["timeout"] = timeout
        total_timeout += timeout
        files = check.get("source_files")
        if not isinstance(files, list) or not files:
            raise ValueError("check source_files must explicitly cover its trusted implementation")
        pins = {}
        for name in files:
            file = (source.parent / name).resolve(strict=True)
            if file.is_relative_to(workspace) or not file.is_file():
                raise ValueError("check source must be a file outside the task workspace")
            pins[str(file)] = hashlib.sha256(file.read_bytes()).hexdigest()
        check["pins"] = pins
        # {policy_dir} is resolved once; {workspace} is expanded per evaluation.
        check["argv"] = [a.replace("{policy_dir}", str(source.parent)) for a in argv]
    if coverage != set(criteria):
        raise ValueError("uncovered acceptance criteria")
    if total_timeout > 15:
        raise ValueError("combined check timeout exceeds the host controller's 15 second budget")
    fixtures = policy.get("calibration")
    if not isinstance(fixtures, list) or {f.get("expect") for f in fixtures if isinstance(f, dict)} != {"pass", "fail"}:
        raise ValueError("both known-correct and known-incorrect calibration fixtures required")
    if total_timeout * len(fixtures) > 20:
        raise ValueError("calibration command timeouts exceed the host command's 20 second budget")
    for fixture in fixtures:
        if not isinstance(fixture, dict) or fixture.get("expect") not in {"pass", "fail"}:
            raise ValueError("invalid calibration expectation")
        root = (source.parent / fixture["path"]).resolve(strict=True)
        if not root.is_dir() or root.is_relative_to(workspace) or workspace.is_relative_to(root):
            raise ValueError("calibration fixtures must be separate from the task workspace")
        fixture.update(path=str(root), sha256=tree_digest(root))
    policy["source"] = str(source)
    policy["max_turns"] = int(policy.get("max_turns", 12))
    if not 1 <= policy["max_turns"] <= 1000:
        raise ValueError("max_turns must be within [1, 1000]")
    policy["fingerprint"] = digest({k: v for k, v in policy.items() if k != "fingerprint"})
    return policy


def run_command(argv: list[str], root: Path, timeout: float) -> dict:
    result = subprocess.run(argv, cwd=root, capture_output=True, text=True,
                            errors="replace", timeout=timeout)
    return {"exit_code": result.returncode, "output": (result.stdout + result.stderr)[-4000:]}


def assess(root: Path, policy: dict, *, runner: Callable = run_command) -> dict:
    """Measure configured criteria. No model assertion is an acceptance input."""
    root = root.resolve()
    observations = []
    try:
        expected = policy["fingerprint"]
        if digest({k: v for k, v in policy.items() if k != "fingerprint"}) != expected:
            raise ValueError("policy fingerprint mismatch")
        before = manifest(root, policy["artifacts"])
        for check in policy["checks"]:
            for file, sha in check["pins"].items():
                if hashlib.sha256(Path(file).read_bytes()).hexdigest() != sha:
                    raise ValueError("check source changed")
            result = runner([a.replace("{workspace}", str(root)) for a in check["argv"]], root, check["timeout"])
            if type(result.get("exit_code")) is not int:
                raise ValueError("check returned no measured integer exit code")
            observations.append({"id": check["id"], "covers": check["covers"],
                                 "verdict": "pass" if result["exit_code"] == 0 else "fail", **result})
            for file, sha in check["pins"].items():
                if hashlib.sha256(Path(file).read_bytes()).hexdigest() != sha:
                    raise ValueError("check source changed during evaluation")
        after = manifest(root, policy["artifacts"])
        conflicting = [criterion for criterion in policy["criteria"]
                       if len({r["verdict"] for r in observations if criterion in r["covers"]}) > 1]
        if conflicting:
            verdict, reason = "disputed", "checkers disagree on the same criteria"
        elif before != after:
            verdict, reason = "unknown", "artifacts changed during evaluation"
        elif not all(before.values()) or any(r["verdict"] == "fail" for r in observations):
            verdict, reason = "fail", "configured acceptance not met"
        else:
            verdict, reason = "pass", "configured checks passed within their declared coverage"
        return {"verdict": verdict, "reason": reason, "policy": expected, "scope": policy["scope"],
                "checks": observations, "artifacts": after, "conflicting_criteria": conflicting}
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        return {"verdict": "unknown", "reason": f"{type(exc).__name__}: {exc}",
                "policy": policy.get("fingerprint"), "checks": observations}


def calibrate(policy: dict, *, runner: Callable = run_command) -> dict:
    rows = []
    for fixture in policy["calibration"]:
        source = Path(fixture["path"])
        if tree_digest(source) != fixture["sha256"]:
            return {"valid": False, "policy": policy["fingerprint"], "reason": "calibration fixture changed", "cases": rows}
        with tempfile.TemporaryDirectory(prefix="supergoal-calibration-") as directory:
            root = Path(directory) / "workspace"
            shutil.copytree(source, root)
            result = assess(root, policy, runner=runner)
            rows.append({"fixture": fixture["sha256"], "expected": fixture["expect"], "observed": result})
    return {"valid": all(r["expected"] == r["observed"]["verdict"] for r in rows),
            "policy": policy["fingerprint"], "cases": rows,
            "scope": "These fixtures only; calibration does not establish complete semantic coverage."}


def verify_policy(root: Path, policy: dict, calibration: dict, *, runner: Callable = run_command) -> dict:
    if calibration.get("valid") is not True or calibration.get("policy") != policy.get("fingerprint"):
        return {"verdict": "unknown", "reason": "valid calibration for this exact policy required"}
    return assess(root, policy, runner=runner)
