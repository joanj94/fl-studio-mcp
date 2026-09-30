"""Running the piano roll script from FL's menu: script position and the checked key walk."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fl_studio_mcp.tools import piano_roll
from fl_studio_mcp.utils import piano_roll_menu

STOCK = ("Arpeggiator", "Euclidean", "Humanize", "Note repeater sprinkler")


def _scripts(folder: Path, *names: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / f"{name}.pyscript").write_text("")
    return folder


def test_scripts_after_merges_folders_and_sorts_by_name(tmp_path):
    user = _scripts(tmp_path / "user", "ComposeWithLLM", "zebra")
    stock = _scripts(tmp_path / "stock", *STOCK)
    (user / "mcp_request.json").write_text("[]")

    # Arpeggiator, ComposeWithLLM, Euclidean, Humanize, Note repeater sprinkler, zebra
    assert piano_roll_menu.scripts_after("ComposeWithLLM", [user, stock]) == 4
    assert piano_roll_menu.scripts_after("zebra", [user, stock]) == 0


def test_scripts_after_is_none_for_a_script_that_is_not_installed(tmp_path):
    stock = _scripts(tmp_path / "stock", *STOCK)

    assert piano_roll_menu.scripts_after("ComposeWithLLM", [stock, tmp_path / "missing"]) is None


class FakeFL:
    """FL's menus as the key walk sees them: the arrow opens one, T a second, Enter closes all."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.menus = 0
        self.arrow: tuple[int, int] | None = (901, 314)
        self.opens_menu = True
        self.has_tools_menu = True
        self.right_opens_a_submenu = False
        self.position = (0, 0)

    # mouse
    def click(self, button: str) -> None:
        self.events.append(f"click {self.position}")
        if self.opens_menu:
            self.menus = 1

    # keyboard
    def tap(self, key: str) -> None:
        self.events.append(key)
        if key == "t" and self.menus == 1 and self.has_tools_menu:
            self.menus = 2
        elif key == "right" and self.right_opens_a_submenu:
            self.menus += 1
        elif key == "enter":
            self.menus = 0
        elif key == "esc":
            self.menus = max(0, self.menus - 1)


@pytest.fixture
def fl(monkeypatch, tmp_path) -> FakeFL:
    fake = FakeFL()
    keys = SimpleNamespace(right="right", up="up", enter="enter", esc="esc")
    user = _scripts(tmp_path / "user", "ComposeWithLLM")
    stock = _scripts(tmp_path / "stock", *STOCK)

    monkeypatch.setattr(piano_roll_menu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(piano_roll_menu, "_devices", lambda: (fake, keys, fake, "left"))
    monkeypatch.setattr(piano_roll_menu, "_script_folders", lambda: [user, stock])
    monkeypatch.setattr(piano_roll_menu, "get_trigger", lambda: SimpleNamespace(focus=lambda: True))
    monkeypatch.setattr(piano_roll_menu, "MENU_TIMEOUT", 0.0)
    monkeypatch.setattr(piano_roll_menu.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(piano_roll_menu.fl_windows, "piano_roll_menu_arrow", lambda: fake.arrow)
    monkeypatch.setattr(piano_roll_menu.fl_windows, "popup_menu_count", lambda: fake.menus)
    monkeypatch.setattr(piano_roll_menu.fl_windows, "move_piano_roll_into_view", lambda: False)
    return fake


def test_run_script_walks_the_menu_to_the_script(fl):
    assert piano_roll_menu.run_script_from_menu() is True
    # three stock scripts sort after ComposeWithLLM: wrap to the last entry, then up three more
    assert fl.events == ["click (901, 314)", "t", "right", "up", "up", "up", "up", "enter"]


def test_run_script_needs_a_visible_piano_roll(fl):
    fl.arrow = None

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_moves_a_covered_piano_roll_into_view(fl, monkeypatch):
    fl.arrow = None

    def move() -> bool:
        fl.arrow = (429, 137)
        return True

    monkeypatch.setattr(piano_roll_menu.fl_windows, "move_piano_roll_into_view", move)

    assert piano_roll_menu.run_script_from_menu() is True
    assert fl.events[0] == "click (429, 137)"


def test_run_script_does_nothing_while_another_menu_is_open(fl):
    fl.menus = 1

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_sends_no_keys_if_the_menu_did_not_open(fl):
    fl.opens_menu = False  # e.g. a dialog is in the way

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == ["click (901, 314)"]


def test_run_script_backs_out_if_the_tools_submenu_did_not_open(fl):
    fl.has_tools_menu = False

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == ["click (901, 314)", "t", "esc"]
    assert fl.menus == 0


def test_run_script_does_not_press_enter_if_the_walk_left_the_tools_submenu(fl):
    fl.right_opens_a_submenu = True  # the first entry was itself a submenu

    assert piano_roll_menu.run_script_from_menu() is False
    assert "enter" not in fl.events
    assert fl.menus == 0  # and every menu was closed again


def test_run_script_closes_the_menus_when_a_key_fails(fl, monkeypatch):
    taps = fl.tap

    def failing_tap(key: str) -> None:
        if key == "right":
            raise RuntimeError("keyboard gone")
        taps(key)

    monkeypatch.setattr(fl, "tap", failing_tap)

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.menus == 0


def test_scripts_in_sub_folders_make_the_position_unknown(tmp_path):
    user = _scripts(tmp_path / "user", "ComposeWithLLM")
    _scripts(user / "More", "Extra")

    assert piano_roll_menu.scripts_after("ComposeWithLLM", [user]) is None


def test_run_script_needs_the_script_to_be_installed(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(piano_roll_menu, "_script_folders", lambda: [tmp_path / "stock"])

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_is_windows_only(fl, monkeypatch):
    monkeypatch.setattr(piano_roll_menu.platform, "system", lambda: "Darwin")

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_survives_a_device_failure(fl, monkeypatch):
    def broken():
        raise RuntimeError("no display")

    monkeypatch.setattr(piano_roll_menu, "_devices", broken)

    assert piano_roll_menu.run_script_from_menu() is False


# --- the piano roll trigger falls back to the menu -------------------------


class SilentTrigger:
    is_supported = True
    platform = "Windows"
    keystroke = "Ctrl+Alt+Y"

    def trigger(self, delay: float = 0) -> bool:
        return True


def test_trigger_runs_the_script_from_the_menu_when_the_shortcut_gets_no_answer(
    settings_dir, monkeypatch
):
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.05)
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: SilentTrigger())

    def script_runs_from_menu() -> bool:
        piano_roll._get_response_file().write_text(
            '{"status": "success", "request_ids": ["a"], "notes_added": 3}'
        )
        return True

    monkeypatch.setattr(piano_roll, "run_script_from_menu", script_runs_from_menu)

    response, message = piano_roll._trigger(["a"])

    assert response["notes_added"] == 3
    assert "3 added" in message


def test_trigger_still_warns_when_the_menu_run_gets_no_answer_either(settings_dir, monkeypatch):
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.05)
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: SilentTrigger())
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: True)

    response, message = piano_roll._trigger(["a"])

    assert response is None
    assert "did not respond" in message
