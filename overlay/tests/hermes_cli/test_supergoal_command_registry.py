from hermes_cli.commands import resolve_command
from hermes_cli.config import DEFAULT_CONFIG


def test_supergoal_registered_with_alias():
    assert resolve_command("supergoal").name == "supergoal"
    assert resolve_command("sgoal").name == "supergoal"


def test_supergoal_has_separate_default_budget():
    assert DEFAULT_CONFIG["goals"]["max_turns"] == 20
    assert DEFAULT_CONFIG["goals"]["super_max_turns"] == 240
