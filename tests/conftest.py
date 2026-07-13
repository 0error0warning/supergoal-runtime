from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


_COLLECTION_HOME = Path(tempfile.mkdtemp(prefix="supergoal-pytest-"))


def pytest_configure() -> None:
    """Set a disposable Hermes home before test modules are imported."""

    os.environ["HERMES_HOME"] = str(_COLLECTION_HOME)


@pytest.fixture(autouse=True)
def _isolate_hermes_home_per_test(tmp_path, monkeypatch):
    """Prevent every test, including host integration tests, from touching production."""

    home = tmp_path / "hermes-home"
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home
