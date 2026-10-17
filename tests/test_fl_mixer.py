"""Deleting a mixer effect through the slot's menu (utils/fl_mixer.py) and the tool."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import register_browser_tools
from fl_studio_mcp.utils import connection, fl_mixer, fl_windows
from tests.fakes import ToolCollector

PANEL = 77
SLOT_PX = 19


class FakeMixer:
    """A mixer's slots, the slot menu (Replace, Delete..., ...) and the Confirm box."""

    def __init__(self) -> None:
        self.effects = {10: {0: "EQ", 1: "Limiter", 2: "Compressor"}}
        self.selected = 0
        self.menu_slot: int | None = None
        self.entry = 0
        self.message: str | None = None
        self.pending: int | None = None
        self.box_answer = "Confirm"
        self.sent: list[str] = []

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        params = params or {}
        self.sent.append(action)
        if action == "mixer.getTrackEffects":
            slots = self.effects.get(params["track"], {})
            return {"success": True, "effects": [{"slot": s, "plugin": n}
                                                 for s, n in sorted(slots.items())]}
        if action == "mixer.selectTrack":
            previous, self.selected = self.selected, params["track"]
            return {"success": True, "track": self.selected, "previous": previous}
        return {"success": True}

    def click(self, hwnd: int, x: int, y: int) -> bool:
        assert hwnd == PANEL and x == fl_mixer.ARROW_X
        self.menu_slot, self.entry = y // SLOT_PX, 0
        return True

    def press(self, key: int, menus: int = 1, chooses: bool = False) -> bool:
        if self.menu_slot is None:
            return False
        self.entry = {fl_windows.VK_HOME: 0, fl_windows.VK_DOWN: self.entry + 1}[key]
        return True

    def enter(self) -> bool:
        if self.entry == 1:  # "Delete..."
            self.pending, self.message = self.menu_slot, self.box_answer
        self.menu_slot = None
        return True

    def ok(self, position: int) -> bool:
        assert position == 0
        if self.message == "Confirm":
            del self.effects[self.selected][self.pending]
        self.message = None
        return True


@pytest.fixture
def fake(monkeypatch) -> FakeMixer:
    mix = FakeMixer()
    monkeypatch.setattr(connection, "get_connection", lambda: mix)
    monkeypatch.setattr(fl_mixer.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_mixer, "SELECT_SETTLE", 0)
    monkeypatch.setattr(fl_mixer, "CONFIRM_TIMEOUT", 0.05)
    monkeypatch.setattr(fl_mixer, "DELETE_TIMEOUT", 0.05)
    monkeypatch.setattr(fl_windows, "mixer_slot_panel", lambda: (PANEL, 10 * SLOT_PX))
    monkeypatch.setattr(fl_windows, "popup_menu_count", lambda: int(mix.menu_slot is not None))
    monkeypatch.setattr(fl_windows, "click_in_window", mix.click)
    monkeypatch.setattr(fl_windows, "press_in_popup_menu", mix.press)
    monkeypatch.setattr(fl_windows, "pointer_over_menu", lambda: False)
    monkeypatch.setattr(fl_windows, "press_enter_in_popup_menu", mix.enter)
    monkeypatch.setattr(fl_windows, "close_popup_menus", lambda: setattr(mix, "menu_slot", None))
    monkeypatch.setattr(fl_windows, "open_message", lambda: mix.message)
    monkeypatch.setattr(fl_windows, "click_message_button", mix.ok)
    monkeypatch.setattr(fl_windows, "close_messages", lambda: setattr(mix, "message", None))
    return mix


def test_an_effect_is_deleted_and_the_selection_put_back(fake):
    assert fl_mixer.delete_effect(10, 1) == "Limiter"

    assert fake.effects[10] == {0: "EQ", 2: "Compressor"}
    assert fake.selected == 0 and fake.menu_slot is None and fake.message is None


def test_an_empty_slot_or_a_bad_one_is_refused_before_anything_is_clicked(fake):
    for slot in (5, 10, True):
        with pytest.raises(fl_mixer.MixerError):
            fl_mixer.delete_effect(10, slot)
    assert "mixer.selectTrack" not in fake.sent


def test_another_box_is_closed_and_nothing_deleted(fake):
    fake.box_answer = "Something else"

    with pytest.raises(fl_mixer.MixerError, match="instead of its delete confirmation"):
        fl_mixer.delete_effect(10, 2)

    assert fake.effects[10] == {0: "EQ", 1: "Limiter", 2: "Compressor"}
    assert fake.message is None and fake.selected == 0


def test_the_tool(fake):
    collector = ToolCollector()
    register_browser_tools(collector)

    assert collector.tools["fl_delete_effect"](10, 0) == {
        "track": 10, "slot": 0, "deleted": "EQ"}
    assert "no effect in slot 0" in collector.tools["fl_delete_effect"](10, 0)["error"]


def test_select_track_reports_the_one_selected_before(controller, fl_modules):
    mixer = fl_modules["mixer"]
    mixer.trackCount.return_value = 18
    mixer.trackNumber.side_effect = [3, 10]
    mixer.getTrackName.return_value = "Insert 10"

    result = controller.dispatch_command("mixer.selectTrack", {"track": 10})

    assert result == {"track": 10, "name": "Insert 10", "previous": 3}
    assert "error" in controller.dispatch_command("mixer.selectTrack", {"track": 17})


def test_nothing_is_tried_off_windows(fake, monkeypatch: Any):
    monkeypatch.setattr(fl_mixer.platform, "system", lambda: "Darwin")

    with pytest.raises(fl_mixer.MixerError, match="Windows"):
        fl_mixer.delete_effect(10, 0)
