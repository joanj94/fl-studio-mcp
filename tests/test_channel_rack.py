"""Deleting channels through the channel rack's menu: the checked walk and the tool."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import channel_setup, register_channel_setup_tools
from fl_studio_mcp.utils import fl_channel_rack
from tests.fakes import ToolCollector

HOME, DOWN, ENTER = 0x24, 0x28, 0x0D


class FakeRack:
    """FL's channel rack: its menu, the confirmation box and the channels."""

    def __init__(self) -> None:
        self.names = ["Kick", "Lead", "Spare A", "Spare B"]
        self.selected: set[int] = set()
        self.events: list = []
        self.menus = 0
        self.entry = 0
        self.message: str | None = None
        self.rack_showing = True
        self.asks = True  # False: "Remember my choice" was ticked, no box comes
        self.clone_enabled = False  # True: the walk ends one entry early, on Clone selected
        self.pointer_on_menu_for = 0
        self.undone = 0

    # --- fl_windows ------------------------------------------------------------

    def click_arrow(self) -> bool:
        if not self.rack_showing:
            return False
        self.events.append("click")
        self.menus = 1
        return True

    def press(self, key: int, menus: int = 1) -> bool:
        if self.menus != menus:
            return False
        self.events.append(key)
        if key == HOME:
            self.entry = 0
        elif key == DOWN:
            self.entry += 1
        elif key == ENTER:
            self.menus = 0
            self._choose()
        return True

    def _choose(self) -> None:
        delete_entry = fl_channel_rack.DOWNS_TO_DELETE + int(self.clone_enabled)
        if self.entry == delete_entry - 1 and self.clone_enabled:
            self.names += [self.names[i] for i in sorted(self.selected)]
        elif self.entry == delete_entry:
            if self.asks:
                self.message = "Confirm"
            else:
                self._delete()

    def _delete(self) -> None:
        self.names = [n for i, n in enumerate(self.names) if i not in self.selected]
        self.selected = set()

    def click_message_button(self, position: int) -> bool:
        if self.message is None:
            return False
        self.events.append(f"button {position}")
        self.message = None
        if position == 0:
            self._delete()
        return True

    def pointer_over_menu(self) -> bool:
        self.pointer_on_menu_for -= 1
        return self.pointer_on_menu_for >= 0

    def close_menus(self) -> None:
        self.menus = 0

    def close_messages(self) -> int:
        closed, self.message = int(self.message is not None), None
        return closed

    # --- connection --------------------------------------------------------------

    def call(self, action: str, params: dict | None = None) -> dict:
        params = params or {}
        if action == "channels.getAll":
            return {"channels": [
                {"index": i, "name": name, "is_selected": i in self.selected}
                for i, name in enumerate(self.names)
            ]}
        if action == "channels.selectOne":
            self.selected = {params["index"]}
        elif action == "channels.select":
            self.selected.add(params["index"])
        elif action == "project.undo":
            self.undone += params["steps"]
        return {}


@pytest.fixture
def rack(monkeypatch) -> FakeRack:
    fake = FakeRack()
    windows = fl_channel_rack.fl_windows
    monkeypatch.setattr(fl_channel_rack.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_channel_rack.time, "sleep", lambda seconds: None)
    for name in ("MENU_TIMEOUT", "CONFIRM_TIMEOUT", "DELETE_TIMEOUT"):
        monkeypatch.setattr(fl_channel_rack, name, 0.0)
    monkeypatch.setattr(fl_channel_rack, "call", fake.call)
    monkeypatch.setattr(channel_setup, "call", fake.call)
    monkeypatch.setattr(windows, "click_channel_rack_menu_arrow", fake.click_arrow)
    monkeypatch.setattr(windows, "press_in_popup_menu", fake.press)
    monkeypatch.setattr(windows, "popup_menu_count", lambda: fake.menus)
    monkeypatch.setattr(windows, "close_popup_menus", fake.close_menus)
    monkeypatch.setattr(windows, "pointer_over_menu", fake.pointer_over_menu)
    monkeypatch.setattr(windows, "open_message", lambda: fake.message)
    monkeypatch.setattr(windows, "click_message_button", fake.click_message_button)
    monkeypatch.setattr(windows, "close_messages", fake.close_messages)
    return fake


@pytest.fixture
def tools(rack, monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(channel_setup, "resolve_channel", lambda channel: (
        channel if isinstance(channel, int) else rack.names.index(channel)))
    collector = ToolCollector()
    register_channel_setup_tools(collector)
    return collector.tools


def test_delete_walks_the_menu_and_confirms(rack):
    assert fl_channel_rack.delete_channels([2, 3]) == 2

    assert rack.names == ["Kick", "Lead"]
    assert rack.events == ["click", HOME, DOWN, DOWN, DOWN, DOWN, ENTER, "button 0"]


def test_delete_works_when_fl_no_longer_asks(rack):
    rack.asks = False

    assert fl_channel_rack.delete_channels([1]) == 3
    assert rack.names == ["Kick", "Spare A", "Spare B"]


def test_delete_needs_the_channel_rack(rack):
    rack.rack_showing = False

    with pytest.raises(fl_channel_rack.ChannelRackError, match="channel rack"):
        fl_channel_rack.delete_channels([1])
    assert len(rack.names) == 4


def test_delete_does_nothing_while_a_message_box_is_open(rack):
    rack.message = "FL Studio trial"

    with pytest.raises(fl_channel_rack.ChannelRackError, match="message box"):
        fl_channel_rack.delete_channels([1])
    assert rack.events == []


def test_delete_waits_for_the_pointer_to_leave_the_menu(rack):
    rack.pointer_on_menu_for = 1

    assert fl_channel_rack.delete_channels([3]) == 3
    assert rack.events.count("click") == 2


def test_delete_gives_up_while_the_pointer_stays_on_the_menu(rack):
    rack.pointer_on_menu_for = 99

    with pytest.raises(fl_channel_rack.ChannelRackError, match="mouse pointer"):
        fl_channel_rack.delete_channels([3])
    assert len(rack.names) == 4
    assert ENTER not in rack.events


def test_a_walk_that_cloned_instead_is_undone_and_reported(rack):
    rack.clone_enabled = True

    with pytest.raises(fl_channel_rack.ChannelRackError, match="not where it was expected"):
        fl_channel_rack.delete_channels([3])
    assert rack.undone == 1


def test_another_boxes_question_is_closed_not_confirmed(rack, monkeypatch):
    monkeypatch.setattr(fl_channel_rack, "CONFIRM_TITLE", "Something else")

    with pytest.raises(fl_channel_rack.ChannelRackError, match="instead of its delete"):
        fl_channel_rack.delete_channels([3])
    assert len(rack.names) == 4
    assert rack.message is None and "button 0" not in rack.events


def test_delete_is_windows_only(rack, monkeypatch):
    monkeypatch.setattr(fl_channel_rack.platform, "system", lambda: "Darwin")

    with pytest.raises(fl_channel_rack.ChannelRackError, match="Windows"):
        fl_channel_rack.delete_channels([1])


def test_tool_deletes_by_index_and_role(tools, rack):
    result = tools["fl_delete_channels"](channels=["Spare A", 3, 3])

    assert result == {"deleted": ["Spare A", "Spare B"], "remaining": 2}


def test_tool_keeps_one_channel(tools, rack):
    result = tools["fl_delete_channels"](channels=[0, 1, 2, 3])

    assert "at least one channel" in result["error"]
    assert len(rack.names) == 4


def test_tool_rejects_an_unknown_index_and_an_empty_list(tools, rack):
    assert "No channel at index 9" in tools["fl_delete_channels"](channels=[9])["error"]
    assert "at least one" in tools["fl_delete_channels"](channels=[])["error"]


def test_tool_reports_why_the_rack_could_not_be_driven(tools, rack):
    rack.rack_showing = False

    assert "channel rack" in tools["fl_delete_channels"](channels=[1])["error"]
