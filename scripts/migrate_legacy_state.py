#!/usr/bin/env python3
"""Import legacy Hermes Supergoal state into the plugin-owned database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from supergoal_runtime.config import get_hermes_home, get_state_db_path
from supergoal_runtime.migration import migrate_legacy_state


def build_parser() -> argparse.ArgumentParser:
    home = get_hermes_home()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=home / "state.db",
        help="legacy Hermes state.db (opened read-only)",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=get_state_db_path(home),
        help="plugin state.db",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    parser.add_argument(
        "--show-paths",
        action="store_true",
        help="include absolute source/target/backup paths in the JSON report",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = migrate_legacy_state(
        args.source,
        target_db=args.target,
        backup=not args.no_backup,
        dry_run=args.dry_run,
        force=args.force,
        include_paths=args.show_paths,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if not report.get("errors") else 2


if __name__ == "__main__":
    raise SystemExit(main())
