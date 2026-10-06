from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml


def _enable_plugin(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"enabled": ["supergoal-runtime"]}}),
        encoding="utf-8",
    )


def test_formal_plugin_manifest_and_package_metadata_exist():
    from supergoal_runtime import __version__

    root = Path(__file__).resolve().parents[2]
    manifest = yaml.safe_load((root / "plugin.yaml").read_text(encoding="utf-8"))
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")

    assert manifest["manifest_version"] == 1
    assert manifest["name"] == "supergoal-runtime"
    assert manifest["kind"] == "standalone"
    assert manifest["provides_tools"] == []
    assert manifest["plugin_abi"] == 1
    assert manifest["version"] == "1.1.1"
    assert __version__ == manifest["version"]
    assert manifest["hermes_min_version"] == "0.20.5"
    assert "post_llm_call" in manifest["provides_hooks"]
    assert "on_session_rotate" not in manifest["provides_hooks"]
    assert 'version = "1.1.1"' in pyproject
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


def test_git_install_enable_disable_uses_real_profile_plugin_flow(tmp_path, monkeypatch):
    from hermes_cli.plugins import PluginManager
    from hermes_cli.plugins_cmd import cmd_disable, cmd_enable, cmd_install

    root = Path(__file__).resolve().parents[2]
    seed = tmp_path / "seed" / "supergoal-runtime"
    seed.mkdir(parents=True)
    for name in (
        "plugin.yaml",
        "__init__.py",
        "pyproject.toml",
        "README.md",
        "LICENSE",
    ):
        shutil.copy2(root / name, seed / name)
    shutil.copytree(root / "supergoal_runtime", seed / "supergoal_runtime")
    subprocess.run(["git", "init", "-q"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.email", "phase2@test.invalid"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.name", "Phase 2 Test"], cwd=seed, check=True)
    subprocess.run(["git", "add", "."], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-qm", "seed plugin"], cwd=seed, check=True)

    home = tmp_path / "profile-install"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump({"plugins": {"scan_on_install": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(
        "hermes_cli.plugins.get_bundled_plugins_dir",
        lambda: tmp_path / "empty-bundled",
    )

    cmd_install(seed.as_uri(), enable=False)
    installed = home / "plugins" / "supergoal-runtime"
    assert (installed / "plugin.yaml").exists()

    disabled_manager = PluginManager()
    disabled_manager.discover_and_load()
    assert disabled_manager._plugins["supergoal-runtime"].enabled is False
    assert "sgx" not in disabled_manager._plugin_commands

    cmd_enable("supergoal-runtime", allow_tool_override=False)
    enabled_manager = PluginManager()
    enabled_manager.discover_and_load()
    assert enabled_manager._plugins["supergoal-runtime"].enabled is True
    assert "sgx" in enabled_manager._plugin_commands

    cmd_disable("supergoal-runtime")
    disabled_again = PluginManager()
    disabled_again.discover_and_load()
    assert disabled_again._plugins["supergoal-runtime"].enabled is False
    assert "sgx" not in disabled_again._plugin_commands
    assert not (home / "supergoal" / "state.db").exists()
