"""Profile-aware configuration helpers for the Supergoal plugin.

No paths are resolved at import time. This is required for Hermes profiles and
for tests that switch ``HERMES_HOME`` within one Python process.
"""

from __future__ import annotations

import os
from pathlib import Path


def get_hermes_home() -> Path:
    """Return the active Hermes home without caching it.

    Prefer the host helper because it honors the task-local ContextVar used by
    multi-profile gateway work. The environment variable is only a fallback for
    standalone package tooling where Hermes is not importable.
    """

    try:
        from hermes_constants import get_hermes_home as host_get_hermes_home

        return Path(host_get_hermes_home()).expanduser().resolve()
    except Exception:
        explicit = os.environ.get("HERMES_HOME")
        if explicit:
            return Path(explicit).expanduser().resolve()
        return Path.home() / ".hermes"


def get_state_db_path(hermes_home: str | Path | None = None) -> Path:
    """Return ``${HERMES_HOME}/supergoal/state.db`` for the active profile."""

    home = (
        Path(hermes_home).expanduser().resolve()
        if hermes_home is not None
        else get_hermes_home()
    )
    return home / "supergoal" / "state.db"
