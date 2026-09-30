"""Running the piano roll script from FL's menu: script position and the checked key walk."""

from __future__ import annotations

from pathlib import Path

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


def test_scripts_in_sub_folders_make_the_position_unknown(tmp_path):
    user = _scripts(tmp_path / "user", "ComposeWithLLM")
    _scripts(user / "More", "Extra")

    assert piano_roll_menu.scripts_after("ComposeWithLLM", [user]) is None


class FakeFL:
    """FL's menus as the walk sees them: the arrow opens one, T a second, Enter closes all."""

    def __init__(self) -> None:
        self.events: list = []
        self.menus = 0
        self.script_dialogs = 0
        self.piano_roll_showing = True
        self.opens_menu = True
        self.has_tools_menu = True
        self.end_opens_a_submenu = False
        self.pointer_on_menu_for = 0  # walks during which the pointer is over the menu

    def click(self) -> bool:
        if not self.piano_roll_showing:
            return False
        self.events.append("click")
        if self.opens_menu:
            self.menus = 1
        return True

    def press(self, key: int, menus: int = 1) -> bool:
        if self.menus != menus:
            return False
        self.events.append(key)
        if key == T_KEY and self.has_tools_menu:
            self.menus = 2
        elif key == END and self.end_opens_a_submenu:
            self.menus += 1
        elif key == ENTER:
            self.menus = 0
        return True

    def pointer_over_menu(self) -> bool:
        self.pointer_on_menu_for -= 1
        return self.pointer_on_menu_for >= 0

    def close_windows(self, class_name: str) -> None:
        self.script_dialogs = 0

    def close_menus(self) -> None:
        if self.menus:
            self.events.append("close")
        self.menus = 0


T_KEY, END, UP, ENTER = ord("T"), 0x23, 0x26, 0x0D


@pytest.fixture
def fl(monkeypatch, tmp_path) -> FakeFL:
    fake = FakeFL()
    user = _scripts(tmp_path / "user", "ComposeWithLLM")
    stock = _scripts(tmp_path / "stock", *STOCK)
    windows = piano_roll_menu.fl_windows

    monkeypatch.setattr(piano_roll_menu.platform, "system", lambda: "Windows")
    monkeypatch.setattr(piano_roll_menu, "_script_folders", lambda: [user, stock])
    monkeypatch.setattr(piano_roll_menu, "MENU_TIMEOUT", 0.0)
    monkeypatch.setattr(piano_roll_menu.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(windows, "click_piano_roll_menu_arrow", fake.click)
    monkeypatch.setattr(windows, "press_in_popup_menu", fake.press)
    monkeypatch.setattr(windows, "popup_menu_count", lambda: fake.menus)
    monkeypatch.setattr(windows, "close_popup_menus", fake.close_menus)
    monkeypatch.setattr(windows, "pointer_over_menu", fake.pointer_over_menu)
    monkeypatch.setattr(windows, "window_count", lambda cls: fake.script_dialogs)
    monkeypatch.setattr(windows, "close_windows", fake.close_windows)
    return fake


def test_run_script_walks_the_menu_to_the_script(fl):
    assert piano_roll_menu.run_script_from_menu() is True
    # three stock scripts sort after ComposeWithLLM: End is the last entry, then up three
    assert fl.events == ["click", T_KEY, END, UP, UP, UP, ENTER]


def test_run_script_needs_a_piano_roll(fl):
    fl.piano_roll_showing = False

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_does_nothing_while_another_menu_is_open(fl):
    fl.menus = 1

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_sends_no_keys_if_the_menu_did_not_open(fl):
    fl.opens_menu = False  # e.g. a dialog is in the way

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == ["click"]


def test_run_script_backs_out_if_the_tools_submenu_did_not_open(fl):
    fl.has_tools_menu = False

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == ["click", T_KEY, "close"]


def test_run_script_does_not_press_enter_if_the_walk_left_the_tools_submenu(fl):
    fl.end_opens_a_submenu = True  # something opened that the walk doesn't know

    assert piano_roll_menu.run_script_from_menu() is False
    assert ENTER not in fl.events
    assert fl.menus == 0  # and every menu was closed again


def test_run_script_closes_the_menus_when_a_key_fails(fl, monkeypatch):
    press = fl.press

    def failing_press(key: int, menus: int = 1) -> bool:
        if key == END:
            raise RuntimeError("window gone")
        return press(key, menus)

    monkeypatch.setattr(piano_roll_menu.fl_windows, "press_in_popup_menu", failing_press)

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.menus == 0


def test_run_script_waits_for_the_pointer_to_leave_the_menu(fl):
    fl.pointer_on_menu_for = 2

    assert piano_roll_menu.run_script_from_menu() is True
    assert fl.events.count("click") == 3 and fl.events.count(ENTER) == 1


def test_run_script_gives_up_while_the_pointer_stays_on_the_menu(fl):
    fl.pointer_on_menu_for = 99

    assert piano_roll_menu.run_script_from_menu() is False
    assert ENTER not in fl.events and fl.menus == 0


def test_another_scripts_settings_window_is_closed(fl):
    fl.script_dialogs = 1

    assert piano_roll_menu.close_script_dialogs() == 1
    assert fl.script_dialogs == 0
    assert piano_roll_menu.close_script_dialogs() == 0


def test_run_script_needs_the_script_to_be_installed(fl, monkeypatch, tmp_path):
    monkeypatch.setattr(piano_roll_menu, "_script_folders", lambda: [tmp_path / "stock"])

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


def test_run_script_is_windows_only(fl, monkeypatch):
    monkeypatch.setattr(piano_roll_menu.platform, "system", lambda: "Darwin")

    assert piano_roll_menu.run_script_from_menu() is False
    assert fl.events == []


# --- the piano roll trigger: the menu first, the shortcut where it can't be used ---


class Shortcut:
    is_supported = True
    platform = "Windows"
    keystroke = "Ctrl+Alt+Y"

    def __init__(self) -> None:
        self.sent = 0

    def trigger(self, delay: float = 0) -> bool:
        self.sent += 1
        return True


@pytest.fixture
def shortcut(settings_dir, monkeypatch) -> Shortcut:
    fake = Shortcut()
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.05)
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: fake)
    return fake


def _answer() -> None:
    piano_roll._get_response_file().parent.mkdir(parents=True, exist_ok=True)
    piano_roll._get_response_file().write_text(
        '{"status": "success", "request_ids": ["a"], "notes_added": 3}'
    )


def test_trigger_runs_the_script_from_the_menu_without_the_shortcut(shortcut, monkeypatch):
    def script_runs_from_menu() -> bool:
        _answer()
        return True

    monkeypatch.setattr(piano_roll, "run_script_from_menu", script_runs_from_menu)

    response, message = piano_roll._trigger(["a"])

    assert response["notes_added"] == 3
    assert "3 added" in message
    assert shortcut.sent == 0  # FL was never brought to the front


def test_trigger_uses_the_shortcut_when_the_menu_cannot_be_used(shortcut, monkeypatch):
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: False)
    _answer()

    response, _ = piano_roll._trigger(["a"])

    assert response["notes_added"] == 3
    assert shortcut.sent == 1


def test_trigger_tells_when_the_walk_reached_another_script(shortcut, monkeypatch):
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: True)
    monkeypatch.setattr(piano_roll, "close_script_dialogs", lambda: 1)

    response, message = piano_roll._trigger(["a"])

    assert response is None
    assert "another piano roll script" in message and "closed without applying" in message


def test_trigger_warns_when_the_menu_run_gets_no_answer(shortcut, monkeypatch):
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: True)
    monkeypatch.setattr(piano_roll, "close_script_dialogs", lambda: 0)

    response, message = piano_roll._trigger(["a"])

    assert response is None
    assert "did not respond" in message
    assert shortcut.sent == 0  # the shortcut would re-run whatever the menu walk ran
