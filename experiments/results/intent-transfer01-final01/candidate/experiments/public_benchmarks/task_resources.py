"""Audit pinned Terminal-Bench task metadata without reading solutions/tests.

The upstream tree is a GitHub git/trees response saved as {repo, sha, tree}.
Downloaded task.toml files must match its Git blob hashes. The output is a
resource eligibility catalogue, never a claim that environments passed preflight.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tomllib
from collections import Counter
from pathlib import Path


def resource_reasons(environment: dict, limits: dict) -> list[str]:
    reasons = []
    for key, limit in limits.items():
        value = environment.get(key)
        if type(value) not in (int, float) or value < 0 or (key != "gpus" and value == 0):
            reasons.append(f"missing_or_invalid:{key}")
        elif value > limit:
            reasons.append(f"exceeds:{key}:{value}>{limit}")
    return reasons


def audit(source: Path, tree: dict, limits: dict) -> dict:
    if tree.get("repo") != "harbor-framework/terminal-bench-2-1":
        raise ValueError("Expected official Terminal-Bench 2.1 repository")
    if not re.fullmatch(r"[0-9a-f]{40}", tree.get("sha", "")):
        raise ValueError("A complete upstream commit is required")
    files = sorted((item for item in tree["tree"] if item["type"] == "blob"
                    and re.fullmatch(r"tasks/[^/]+/task\.toml", item["path"])),
                   key=lambda item: item["path"])
    if not files or len({item["path"] for item in files}) != len(files):
        raise ValueError("Missing or duplicated task metadata in upstream tree")
    rows = []
    for item in files:
        data = (source / item["path"]).read_bytes()
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        if digest != item["sha"]:
            raise ValueError(f"Git blob hash mismatch: {item['path']}")
        task = tomllib.loads(data.decode("utf-8"))
        environment = task.get("environment", {})
        reasons = resource_reasons(environment, limits)
        rows.append({
            "task_id": item["path"].split("/")[1],
            "metadata_path": item["path"], "metadata_git_blob_sha": digest,
            "category": task.get("metadata", {}).get("category"),
            "difficulty": task.get("metadata", {}).get("difficulty"),
            "resources": {key: environment.get(key) for key in limits},
            "docker_image_tag": environment.get("docker_image"),
            "agent_timeout_sec": task.get("agent", {}).get("timeout_sec"),
            "verifier_timeout_sec": task.get("verifier", {}).get("timeout_sec"),
            "matches_declared_resource_limits": not reasons,
            "exclusion_reasons": reasons,
        })
    matching = [row for row in rows if row["matches_declared_resource_limits"]]
    return {
        "schema": 1, "source_repo": tree["repo"], "source_commit": tree["sha"],
        "status": "metadata_audit_only", "benchmark_ready": False,
        "formal_task_selection_frozen": False,
        "limits_in_upstream_metadata_units": limits,
        "limitations": ["Image size and build peak are not included in storage_mb.",
                        "Resource declarations are not measured peak usage or oracle results.",
                        "Image tags remain mutable until digests are pinned.",
                        "Categories and difficulty are upstream labels, not study results."],
        "summary": {"official_task_count": len(rows), "matching_task_count": len(matching),
                    "matching_categories": dict(sorted(Counter(row["category"] or "unspecified" for row in matching).items())),
                    "matching_difficulty": dict(sorted(Counter(row["difficulty"] or "unspecified" for row in matching).items()))},
        "tasks": rows,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--upstream-tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpus", type=int, default=2)
    parser.add_argument("--memory-mb", type=int, default=4096)
    parser.add_argument("--storage-mb", type=int, default=10240)
    args = parser.parse_args()
    limits = {"cpus": args.cpus, "memory_mb": args.memory_mb,
              "storage_mb": args.storage_mb, "gpus": 0}
    if min(args.cpus, args.memory_mb, args.storage_mb) <= 0:
        parser.error("Resource limits must be positive")
    result = audit(args.source, json.loads(args.upstream_tree.read_text(encoding="utf-8")), limits)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
