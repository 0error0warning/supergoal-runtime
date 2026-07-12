from __future__ import annotations

import shutil
from pathlib import Path

import yaml


def _enable_plugin(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["supergoal-runtime"]}}),
        encoding="utf-8",
    )


def test_formal_plugin_manifest_and_package_metadata_exist():
    root = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert manifest["name"] == "supergoal-runtime"
    assert manifest["kind"] == "standalone"
    assert manifest["provides_tools"] == []
    assert manifest["plugin_abi"] == 1
    assert "on_session_rotate" in manifest["provides_hooks"]
    assert '"hermes_agent.plugins"' in pyproject
    assert 'supergoal-runtime = "supergoal_runtime.plugin"' in pyproject


def test_enabled_directory_plugin_is_discovered_without_touching_real_home(
    tmp_path, monkeypatch
):
    from hermes_cli.plugins import PluginManager

    root = Path(__file__).resolve().parents[2]
    home = tmp_path / "profile-a"
    plugin_dir = home / "plugins" / "supergoal-runtime"
    plugin_dir.parent.mkdir(parents=True)
    shutil.copytree(root, plugin_dir, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    _enable_plugin(home)

    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: tmp_path / "empty-bundled",
    )

    manager = PluginManager()
    manager.discover_and_load()

    loaded = manager._plugins["supergoal-runtime"]
    assert loaded.enabled is True
    assert "sgx" in manager._plugin_commands
    assert manager._turn_controllers
    assert not (home / "supergoal" / "state.db").exists(), (
        "plugin discovery/import must stay side-effect free"
    )
