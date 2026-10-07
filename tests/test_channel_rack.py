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
        self.plugins = [None, "Sytrus", None, None]
        self.cut_itself = [False, True, False, False]
        self.scrolled = 0  # rows the list is scrolled down
        self.panel_height = 516
        self.button_menu = False  # the open menu is a channel button's, not the rack's
        self.one_at_a_time = False  # True: each read finds one more of the channels gone
        self.going: list[int] = []

    # --- fl_windows ------------------------------------------------------------

    def click_arrow(self) -> bool:
        if not self.rack_showing:
            return False
        self.events.append("click")
        self.menus, self.button_menu = 1, False
        return True

    def channel_list_panel(self) -> tuple[int, int] | None:
        return (77, self.panel_height) if self.rack_showing else None

    def right_click(self, hwnd: int, x: int, y: int) -> bool:
        row = (y - 17) // 30 + self.scrolled  # seen live: rows of 30 px, the first from 17
        self.events.append(f"right click {row}")
        if 0 <= row < len(self.names):
            self.selected = {row}
            self.menus, self.button_menu, self.entry = 1, True, 0
        return True

    def _cut_itself_entry(self) -> int:
        (channel,) = self.selected
        return 7 if self.plugins[channel] is None else 6

    def menu_pixels(self) -> tuple[int, int, bytes] | None:
        """The button's menu: 10 entries of 10 px, a check mark left of the toggle's text."""
        if not self.menus:
            return None
        (channel,) = self.selected
        rows = []
        for entry in range(10):
            highlighted = entry == self.entry
            background, ink = (100, 230) if highlighted else (190, 20)
            mark = background
            if entry == self._cut_itself_entry():
                mark = ink if self.cut_itself[channel] or highlighted else 170
            line = [background] + [mark] * 4 + [background] * 2 + [ink] * 20 + [background] * 13
            rows += [[background] * 40 if y in (0, 9) else line for y in range(10)]
        return 40, 100, bytes(v for row in rows for pixel in row for v in (pixel,) * 4)

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
        if self.button_menu:
            (channel,) = self.selected
            if self.entry == self._cut_itself_entry():
                self.cut_itself[channel] = not self.cut_itself[channel]
            return
        delete_entry = fl_channel_rack.DOWNS_TO_DELETE + int(self.clone_enabled)
        if self.entry == delete_entry - 1 and self.clone_enabled:
            self.names += [self.names[i] for i in sorted(self.selected)]
        elif self.entry == delete_entry:
            if self.asks:
                self.message = "Confirm"
            else:
                self._delete()

    def _delete(self) -> None:
        if self.one_at_a_time:
            self.going = sorted(self.selected, reverse=True)
        else:
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
            if self.going:
                del self.names[self.going.pop(0)]
            return {"channels": [
                {"index": i, "name": name, "is_selected": i in self.selected,
                 "plugin": self.plugins[i] if i < len(self.plugins) else None}
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
    monkeypatch.setattr(windows, "channel_list_panel", fake.channel_list_panel)
    monkeypatch.setattr(windows, "right_click_in_window", fake.right_click)
    monkeypatch.setattr(fl_channel_rack.fl_capture, "menu_pixels", fake.menu_pixels)
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


def test_delete_waits_for_every_channel_to_go(rack, monkeypatch):
    # Seen live: deleting 3 channels, the rack was read with only the first one gone.
    monkeypatch.setattr(fl_channel_rack, "DELETE_TIMEOUT", 60.0)
    rack.one_at_a_time = True

    assert fl_channel_rack.delete_channels([1, 2, 3]) == 1
    assert rack.names == ["Kick"]
    assert rack.undone == 0


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


def test_cut_itself_is_switched_on_in_the_buttons_menu_and_read_back(rack):
    rack.selected = {3}

    assert fl_channel_rack.set_cut_itself(0, True) is True

    assert rack.cut_itself[0] is True
    walk = ["right click 0", HOME] + [DOWN] * 7
    assert rack.events == walk + [ENTER] + walk
    assert rack.menus == 0 and rack.selected == {3}


def test_cut_itself_that_is_already_right_is_left_alone(rack):
    assert fl_channel_rack.set_cut_itself(1, True) is False
    assert fl_channel_rack.set_cut_itself(2, False) is False

    assert ENTER not in rack.events and rack.cut_itself == [False, True, False, False]


def test_a_plugin_channels_menu_has_cut_itself_one_entry_higher(rack):
    assert fl_channel_rack.set_cut_itself(1, False) is True

    assert rack.cut_itself[1] is False
    assert rack.events[:8] == ["right click 1", HOME] + [DOWN] * 6


def test_the_button_is_found_in_a_scrolled_rack(rack):
    rack.scrolled = 1

    assert fl_channel_rack.set_cut_itself(2, True) is True

    assert rack.events[:2] == ["right click 3", "right click 2"]
    assert rack.cut_itself == [False, True, True, False]


def test_a_channel_out_of_view_is_reported(rack):
    rack.panel_height = 60

    with pytest.raises(fl_channel_rack.ChannelRackError, match="isn't showing in the channel rack"):
        fl_channel_rack.set_cut_itself(3, True)
    assert rack.menus == 0 and ENTER not in rack.events


def test_cut_itself_that_did_not_change_is_reported(rack, monkeypatch):
    monkeypatch.setattr(fl_channel_rack, "DOWNS_TO_CUT_ITSELF", 5)

    with pytest.raises(fl_channel_rack.ChannelRackError, match="did not change"):
        fl_channel_rack.set_cut_itself(0, True)
    assert rack.cut_itself[0] is False and rack.menus == 0


def test_cut_itself_chooses_nothing_under_the_pointer(rack):
    rack.pointer_on_menu_for = 99

    with pytest.raises(fl_channel_rack.ChannelRackError, match="mouse pointer"):
        fl_channel_rack.set_cut_itself(0, True)
    assert ENTER not in rack.events and rack.menus == 0


def test_cut_itself_needs_the_rack_and_a_channel(rack):
    with pytest.raises(fl_channel_rack.ChannelRackError, match="No channel at index 9"):
        fl_channel_rack.set_cut_itself(9, True)
    rack.rack_showing = False
    with pytest.raises(fl_channel_rack.ChannelRackError, match="isn't showing in FL Studio"):
        fl_channel_rack.set_cut_itself(0, True)


def test_a_menu_whose_highlight_did_not_move_cannot_be_read():
    with pytest.raises(fl_channel_rack.ChannelRackError, match="did not move"):
        fl_channel_rack.entry_is_checked(2, bytes(32), bytes(32))


def test_tool_sets_cut_itself_by_role(tools, rack):
    result = tools["fl_set_cut_itself"](channel="Spare A")

    assert result == {"channel": 2, "name": "Spare A", "cut_itself": True, "changed": True}
    assert "No channel" in tools["fl_set_cut_itself"](channel=9)["error"]


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
