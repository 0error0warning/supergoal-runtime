"""Verify local research manifests without running models or remote operations.

Frozen source archives are checked as files, not extracted. Remote Docker CAS
blobs are outside this checkout and outside this integrity check's scope.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def entries_from(manifest: dict) -> list[dict]:
    files = manifest.get("files", manifest.get("sha256"))
    if isinstance(files, dict):
        entries = []
        for name, metadata in files.items():
            item = {"sha256": metadata} if isinstance(metadata, str) else dict(metadata)
            entries.append({**item, "path": name})
    elif isinstance(files, list):
        entries = files
    else:
        raise ValueError("unsupported manifest schema")
    if not entries or any(
        not isinstance(item, dict)
        or not isinstance(item.get("path"), str)
        or not isinstance(item.get("sha256"), str)
        for item in entries
    ):
        raise ValueError("empty or invalid manifest entries")
    return entries


def within(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("manifest path leaves the repository")
    return resolved


def verify(root: Path) -> dict:
    root = root.resolve()
    results = root / "experiments" / "results"
    manifests = sorted(results.glob("*/manifest.json"))
    manifests += sorted(results.glob("*/evidence-manifest.json"))
    manifests.append(root / "experiments" / "baselines" / "archives.json")
    errors = []
    files_checked = 0
    unique_files: set[str] = set()
    relocated = []
    if not results.is_dir() or len(manifests) == 1:
        errors.append({"error": "research evidence capsules are missing"})
    for manifest_path in manifests:
        label = manifest_path.relative_to(root).as_posix()
        try:
            manifest = json.loads(manifest_path.read_bytes())
            entries = entries_from(manifest)
        except (OSError, ValueError, TypeError) as exc:
            errors.append({"manifest": label, "error": str(exc)})
            continue
        for entry in entries:
            try:
                target = within(root, manifest_path.parent / entry["path"])
                # One historical source archive was deliberately stored in
                # baselines; its manifest explicitly records the local path.
                if (
                    not target.is_file()
                    and entry["path"].endswith(".tar.gz")
                    and manifest.get("archive_local_path")
                ):
                    target = within(root, root / manifest["archive_local_path"])
                    relocated.append({"manifest": label, "path": target.relative_to(root).as_posix()})
                raw = target.read_bytes()
                files_checked += 1
                unique_files.add(target.relative_to(root).as_posix())
                if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    raise ValueError("SHA256 mismatch")
                if "bytes" in entry and len(raw) != entry["bytes"]:
                    raise ValueError("byte count mismatch")
            except (OSError, ValueError, TypeError) as exc:
                errors.append({"manifest": label, "path": entry["path"], "error": str(exc)})
    return {
        "scope": "local capsule manifests and baseline archive hashes; no remote blobs or model runs",
        "manifests": len(manifests),
        "file_references_checked": files_checked,
        "unique_files_checked": len(unique_files),
        "explicit_relocations": relocated,
        "errors": errors,
        "passed": not errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    result = verify(parser.parse_args().root)
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
