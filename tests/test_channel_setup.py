"""Adding channels: plugin database lookup, the keystroke sequence and the tools."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fl_studio_mcp.tools import channel_setup, register_channel_setup_tools
from fl_studio_mcp.utils import connection, fl_keys, fl_trigger, plugin_database
from tests.fakes import ToolCollector


@pytest.fixture
def generators(settings_dir: Path) -> Path:
    """A plugin database with three generators, next to the Settings folder."""
    root = settings_dir.parent / "Presets" / "Plugin database" / "Generators"
    for relative in ("Sampler/Sampler.fst", "Synth classic/3x Osc.fst", "New/Fruity Slicer 2.FST",
                     "Synth classic/3x Osc.nfo", "Synth classic/3x Osc.png"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return root


# --- plugin database -------------------------------------------------------


def test_list_generators_reads_names_and_categories(generators):
    assert plugin_database.list_generators() == {
        "3x Osc": "Synth classic",
        "Fruity Slicer 2": "New",
        "Sampler": "Sampler",
    }


def test_list_generators_reports_a_missing_database(settings_dir):
    with pytest.raises(ValueError, match="plugin database was not found"):
        plugin_database.list_generators()


def test_list_generators_reports_an_empty_database(generators):
    for path in generators.glob("*/*"):
        path.unlink()

    with pytest.raises(ValueError, match="No generators"):
        plugin_database.list_generators()


@pytest.mark.parametrize("name", ["3x Osc", "3X OSC", "  3x osc "])
def test_find_generator_ignores_case_and_outer_spaces(generators, name):
    assert plugin_database.find_generator(name) == "3x Osc"


def test_find_generator_rejects_partial_names_with_suggestions(generators):
    with pytest.raises(ValueError, match="Did you mean: Fruity Slicer 2"):
        plugin_database.find_generator("Fruity Slicer")


def test_find_generator_without_a_close_match_points_at_the_list(generators):
    with pytest.raises(ValueError, match="fl_list_generators"):
        plugin_database.find_generator("zzqq")


# --- keystrokes ------------------------------------------------------------


class FakeKeyboard:
    def __init__(self, fail: bool = False) -> None:
        self.events: list[str] = []
        self.fail = fail

    def tap(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("no display")
        self.events.append(f"tap {key}")

    def type(self, text: str) -> None:
        self.events.append(f"type {text}")


class FakeTrigger:
    """FL's window: `focus()` succeeds or not, and focus can be lost after N checks."""

    def __init__(self, can_focus: bool = True, focus_checks_passed: int = 99) -> None:
        self.can_focus = can_focus
        self.focus_checks_passed = focus_checks_passed

    def focus(self) -> bool:
        return self.can_focus

    def has_focus(self) -> bool:
        self.focus_checks_passed -= 1
        return self.focus_checks_passed >= 0


@pytest.fixture
def keyboard(monkeypatch) -> FakeKeyboard:
    fake = FakeKeyboard()
    keys = SimpleNamespace(f8="f8", enter="enter")
    monkeypatch.setattr(fl_keys, "_keyboard", lambda: (fake, keys))
    monkeypatch.setattr(fl_keys.time, "sleep", lambda seconds: None)
    return fake


def _fl_window(monkeypatch, trigger: FakeTrigger) -> None:
    monkeypatch.setattr(fl_keys, "get_trigger", lambda: trigger)


def test_add_generator_keystrokes_opens_the_picker_and_types_the_name(keyboard, monkeypatch):
    _fl_window(monkeypatch, FakeTrigger())

    assert fl_keys.add_generator_keystrokes("3x Osc") is True
    assert keyboard.events == ["tap f8", "type 3x osc", "tap enter"]


def test_add_generator_keystrokes_sends_nothing_without_focus(keyboard, monkeypatch):
    _fl_window(monkeypatch, FakeTrigger(can_focus=False))

    assert fl_keys.add_generator_keystrokes("3x Osc") is False
    assert keyboard.events == []


@pytest.mark.parametrize(
    ("checks_passed", "sent"),
    [(0, []), (1, ["tap f8"]), (2, ["tap f8", "type 3x osc"])],
)
def test_add_generator_keystrokes_stops_when_fl_loses_focus(
    keyboard, monkeypatch, checks_passed, sent
):
    _fl_window(monkeypatch, FakeTrigger(focus_checks_passed=checks_passed))

    assert fl_keys.add_generator_keystrokes("3x Osc") is False
    assert keyboard.events == sent


def test_add_generator_keystrokes_reports_a_keyboard_failure(keyboard, monkeypatch):
    _fl_window(monkeypatch, FakeTrigger())
    keyboard.fail = True

    assert fl_keys.add_generator_keystrokes("3x Osc") is False


@pytest.mark.parametrize(
    ("system", "expected"), [("Windows", "windows"), ("Darwin", "macos"), ("Linux", False)]
)
def test_trigger_focus_uses_the_platform_method(monkeypatch, system, expected):
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: system)
    trigger = fl_trigger.FLStudioTrigger()
    monkeypatch.setattr(trigger, "_focus_fl_studio_windows", lambda: "windows")
    monkeypatch.setattr(trigger, "_focus_fl_studio_macos", lambda: "macos")

    assert trigger.focus() == expected


def test_trigger_has_focus_is_only_checked_on_windows(monkeypatch):
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: "Darwin")

    assert fl_trigger.FLStudioTrigger().has_focus() is True


# --- tools -----------------------------------------------------------------


class FakeRack:
    """Stand-in FL connection holding a channel rack that a keystroke can grow."""

    def __init__(self, names: list[str]) -> None:
        self.names = list(names)
        self.plugins: dict[str, str] = {}
        self.sent: list[tuple[str, dict]] = []
        self.errors: dict[str, str] = {}

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        params = params or {}
        self.sent.append((action, params))
        if action in self.errors:
            return {"success": False, "id": "x", "error": self.errors[action]}
        result: dict[str, Any] = {}
        if action == "channels.getAll":
            result = {"channels": [
                {"index": i, "name": name, "plugin": self.plugins.get(name)}
                for i, name in enumerate(self.names)
            ]}
        elif action == "channels.setName":
            self.names[params["index"]] = params["name"]
        return {"success": True, "id": "x", **result}

    def actions(self) -> list[str]:
        return [action for action, _ in self.sent]


@pytest.fixture
def rack(monkeypatch) -> FakeRack:
    fake = FakeRack(["808 Kick", "808 Clap"])
    monkeypatch.setattr(connection, "get_connection", lambda: fake)
    monkeypatch.setattr(channel_setup, "NEW_CHANNEL_TIMEOUT", 0.0)
    monkeypatch.setattr(channel_setup.time, "sleep", lambda seconds: None)
    return fake


def _fl_reacts(monkeypatch, reaction) -> list[str]:
    """Replace the keystrokes: `reaction(name)` is what FL does with them."""
    sent: list[str] = []

    def keystrokes(name: str) -> bool:
        sent.append(name)
        return reaction(name) is not False

    monkeypatch.setattr(channel_setup, "add_generator_keystrokes", keystrokes)
    return sent


@pytest.fixture
def typed(rack, monkeypatch) -> list[str]:
    """Names sent to the plugin picker; FL answers by adding that channel."""
    return _fl_reacts(monkeypatch, rack.names.append)


@pytest.fixture
def tools(rack, generators) -> dict[str, Any]:
    collector = ToolCollector()
    register_channel_setup_tools(collector)
    return collector.tools


def test_list_generators_tool(tools):
    assert tools["fl_list_generators"]() == {"generators": [
        {"name": "3x Osc", "category": "Synth classic"},
        {"name": "Fruity Slicer 2", "category": "New"},
        {"name": "Sampler", "category": "Sampler"},
    ]}


def test_list_generators_tool_reports_a_missing_database(tools, generators):
    for path in sorted(generators.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    generators.rmdir()

    assert "plugin database" in tools["fl_list_generators"]()["error"]


def test_add_channel_adds_renames_and_closes_the_plugin_window(tools, rack, typed):
    result = tools["fl_add_channel"]("sampler", name="Kick")

    assert result == {"index": 2, "name": "Kick", "plugin": "Sampler"}
    assert typed == ["Sampler"]
    assert rack.names == ["808 Kick", "808 Clap", "Kick"]
    assert ("channels.showEditor", {"index": 2, "show": False}) in rack.sent


def test_add_channel_keeps_the_plugin_name_by_default(tools, rack, typed):
    assert tools["fl_add_channel"]("3x Osc") == {"index": 2, "name": "3x Osc", "plugin": "3x Osc"}
    assert "channels.setName" not in rack.actions()


def test_add_channel_accepts_a_channel_identified_by_its_plugin(tools, rack, monkeypatch):
    rack.plugins["Init"] = "3x Osc"
    _fl_reacts(monkeypatch, lambda name: rack.names.append("Init"))

    assert tools["fl_add_channel"]("3x Osc", name="Lead")["name"] == "Lead"


def test_add_channel_renames_the_new_channel_not_an_older_namesake(tools, rack, typed):
    rack.names[:] = ["Sampler", "808 Clap"]

    assert tools["fl_add_channel"]("Sampler", name="Kick")["index"] == 2
    assert rack.names == ["Sampler", "808 Clap", "Kick"]


def test_add_channel_rejects_unknown_plugins_before_touching_fl(tools, rack, typed):
    result = tools["fl_add_channel"]("zzqq")

    assert "No generator named" in result["error"]
    assert typed == []
    assert rack.sent == []


def test_add_channel_needs_to_read_the_rack_first(tools, rack, typed):
    rack.errors["channels.getAll"] = "FL is not answering"

    assert "channel rack" in tools["fl_add_channel"]("Sampler")["error"]
    assert typed == []


def test_add_channel_survives_a_lost_connection(tools, rack, typed, monkeypatch):
    def dropped(action, params=None, timeout=2.0):
        raise RuntimeError("MIDI port closed")

    monkeypatch.setattr(rack, "send_command", dropped)

    assert "channel rack" in tools["fl_add_channel"]("Sampler")["error"]
    assert typed == []


def test_add_channel_reports_keystrokes_that_could_not_be_sent(tools, rack, monkeypatch):
    _fl_reacts(monkeypatch, lambda name: False)

    assert "keystrokes" in tools["fl_add_channel"]("Sampler")["error"]


def test_add_channel_reports_when_no_channel_appears(tools, rack, monkeypatch):
    _fl_reacts(monkeypatch, lambda name: None)

    result = tools["fl_add_channel"]("Sampler", name="Kick")

    assert "no channel was added" in result["error"]
    assert "channels.setName" not in rack.actions()


def test_add_channel_reports_a_different_plugin_and_does_not_rename(tools, rack, monkeypatch):
    _fl_reacts(monkeypatch, lambda name: rack.names.append("Slicex"))

    result = tools["fl_add_channel"]("Sampler", name="Kick")

    assert "new channel 2 is 'Slicex'" in result["error"]
    assert rack.names[-1] == "Slicex"


@pytest.mark.parametrize("reaction", [
    lambda names: names.insert(0, "Sampler"),  # not added at the end
    lambda names: names.extend(["Sampler", "Sampler"]),  # two channels appeared
    lambda names: names.pop(),  # a channel disappeared
])
def test_add_channel_refuses_to_guess_when_the_rack_changed_oddly(
    tools, rack, monkeypatch, reaction
):
    _fl_reacts(monkeypatch, lambda name: reaction(rack.names))

    result = tools["fl_add_channel"]("Sampler", name="Kick")

    assert "unexpected way" in result["error"]
    assert "Kick" not in rack.names


def test_add_channel_reports_a_failed_rename(tools, rack, typed):
    rack.errors["channels.setName"] = "nope"

    assert "not renamed: nope" in tools["fl_add_channel"]("Sampler", name="Kick")["error"]


def test_add_channel_warns_when_the_plugin_window_stays_open(tools, rack, typed):
    rack.errors["channels.showEditor"] = "nope"

    result = tools["fl_add_channel"]("Sampler", name="Kick")

    assert result["index"] == 2 and result["name"] == "Kick"
    assert "could not be closed: nope" in result["warning"]


def test_wait_for_rack_change_polls_until_the_channel_appears(rack, monkeypatch):
    monkeypatch.setattr(channel_setup, "NEW_CHANNEL_TIMEOUT", 5.0)
    monkeypatch.setattr(channel_setup.time, "sleep", lambda seconds: rack.names.append("Sampler"))
    before = channel_setup._read_rack()

    after = channel_setup._wait_for_rack_change(before)

    assert [channel["name"] for channel in after] == ["808 Kick", "808 Clap", "Sampler"]
    assert rack.actions() == ["channels.getAll"] * 3
