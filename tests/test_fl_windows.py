"""Finding FL's panels and popup menus, against a fake user32/kernel32."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fl_studio_mcp.utils import fl_windows, win_focus

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="EnumWindows callbacks need Windows ctypes"
)

MAIN = 100
FL_EXE = r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe"


class FakeWindows:
    """user32 and kernel32 in one: FL's main window, its panels and any popup menus."""

    def __init__(self) -> None:
        # hwnd: (class, title, rect, owner exe)
        self.top = {
            MAIN: ("TFruityLoopsMainForm", "FL Studio 2026", (0, 0, 1920, 1040), FL_EXE),
            900: ("TQuickPopupMenuWindow", "", (5, 5, 50, 50), r"C:\Apps\other.exe"),
        }
        self.children = {
            201: ("TNewCaption", "Channel rack", (378, 86, 869, 112), FL_EXE),
            202: ("TEventEditForm", "Piano roll -", (886, 299, 1844, 1065), FL_EXE),
            203: ("TNewCaption", "Piano roll -", (890, 303, 1840, 326), FL_EXE),
        }
        self.hidden: set[int] = set()
        self.posted: list[tuple] = []
        self.minimized = False
        self.shown: list[tuple] = []
        self.pointer: tuple[int, int] | None = (5, 5)
        self.texts: list[tuple] = []
        self.dialog_children: dict[int, tuple] = {}
        self.positions: list[int] = []

    def SendMessageTimeoutW(self, hwnd, message, wparam, text, flags, timeout, result):  # noqa: N802
        self.texts.append((hwnd, message, text))
        return 1

    def GetCursorPos(self, point_ref):  # noqa: N802
        if self.pointer is None:
            return 0
        point_ref._obj.x, point_ref._obj.y = self.pointer
        return 1

    def IsIconic(self, hwnd):  # noqa: N802
        return self.minimized

    def ShowWindow(self, hwnd, command):  # noqa: N802
        self.shown.append((hwnd.value, command))
        self.minimized = False

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):  # noqa: N802
        self.shown.append((hwnd.value, after.value, flags))

    def PostMessageW(self, hwnd, message, wparam, lparam):  # noqa: N802
        self.posted.append((hwnd.value, message, wparam))
        self.positions.append(lparam)
        return 1

    def _window(self, hwnd):
        return {**self.top, **self.children, **self.dialog_children}[hwnd]

    def EnumWindows(self, callback, lparam):  # noqa: N802 - Windows API name
        for hwnd in self.top:
            callback(hwnd, lparam)

    def EnumChildWindows(self, parent, callback, lparam):  # noqa: N802
        for hwnd in self.children if parent == MAIN else self.dialog_children:
            callback(hwnd, lparam)

    def IsWindowVisible(self, hwnd):  # noqa: N802
        return hwnd not in self.hidden

    def GetClassNameW(self, hwnd, buffer, size):  # noqa: N802
        buffer.value = self._window(hwnd)[0]

    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return len(self._window(hwnd)[1])

    def GetWindowTextW(self, hwnd, buffer, size):  # noqa: N802
        buffer.value = self._window(hwnd)[1]

    def GetWindowRect(self, hwnd, rect_ref):  # noqa: N802
        rect = rect_ref._obj
        rect.left, rect.top, rect.right, rect.bottom = self._window(hwnd)[2]

    def GetWindowThreadProcessId(self, hwnd, pid_ref):  # noqa: N802
        pid_ref._obj.value = hwnd

    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return pid

    def QueryFullProcessImageNameW(self, process, flags, buffer, size_ref):  # noqa: N802
        buffer.value = self._window(process)[3]
        return 1

    def CloseHandle(self, process):  # noqa: N802
        pass


@pytest.fixture
def windows(monkeypatch) -> FakeWindows:
    fake = FakeWindows()
    monkeypatch.setattr(win_focus, "_libraries", lambda: (fake, fake))
    monkeypatch.setattr(fl_windows, "_chosen_menus", {})
    return fake


def _open_menu(windows: FakeWindows, hwnd: int) -> None:
    windows.top[hwnd] = ("TQuickPopupMenuWindow", "", (890, 326, 1075, 662), FL_EXE)


def test_panel_caption_finds_the_caption_not_the_panel(windows):
    assert fl_windows.panel_caption(windows, MAIN, "Piano roll") == (203, (890, 303, 1840, 326))
    assert fl_windows.panel_caption(windows, MAIN, "Mixer") is None


def test_the_piano_roll_menu_arrow_is_clicked_by_a_posted_click(windows):
    assert fl_windows.click_piano_roll_menu_arrow() is True
    position = (11 << 16) | 11  # y, x inside the caption
    assert windows.posted == [(203, 0x0201, 1), (203, 0x0202, 0)]
    assert windows.positions == [position, position]


def test_the_menu_arrow_needs_a_visible_piano_roll(windows):
    windows.hidden.add(203)

    assert fl_windows.click_piano_roll_menu_arrow() is False
    assert windows.posted == []


def test_nothing_is_found_when_fl_is_not_running(windows):
    del windows.top[MAIN]

    assert fl_windows.click_piano_roll_menu_arrow() is False
    assert fl_windows.fl_install_dir() is None


def test_popup_menu_count_only_counts_fls_visible_menus(windows):
    assert fl_windows.popup_menu_count() == 0  # the other program's menu doesn't count

    _open_menu(windows, 301)
    _open_menu(windows, 302)
    assert fl_windows.popup_menu_count() == 2

    windows.hidden.add(302)
    assert fl_windows.popup_menu_count() == 1


def test_install_dir_is_the_folder_of_fls_program(windows):
    assert fl_windows.fl_install_dir() == Path(FL_EXE).parent


KEY_DOWN, KEY_UP, CLOSE, ENTER, ESCAPE = 0x0100, 0x0101, 0x0010, 0x0D, 0x1B


def test_enter_is_posted_to_the_one_open_menu(windows):
    _open_menu(windows, 301)

    assert fl_windows.press_enter_in_popup_menu() is True
    assert windows.posted == [(301, KEY_DOWN, ENTER), (301, KEY_UP, ENTER)]


def test_a_key_for_a_submenu_goes_to_the_innermost_menu_and_needs_both_open(windows):
    _open_menu(windows, 301)
    assert fl_windows.press_in_popup_menu(0x26, menus=2) is False

    # Windows lists the topmost window first, so the submenu comes before its parent.
    windows.top = {302: windows.top[301], **windows.top}
    assert fl_windows.press_in_popup_menu(0x26, menus=2) is True
    assert windows.posted == [(302, KEY_DOWN, 0x26), (302, KEY_UP, 0x26)]


def test_menus_fading_out_after_a_choice_no_longer_count(windows, monkeypatch):
    _open_menu(windows, 301)
    assert fl_windows.press_enter_in_popup_menu() is True

    assert fl_windows.popup_menu_count() == 0  # its window is still there, fading
    _open_menu(windows, 302)
    assert fl_windows.popup_menu_count() == 1  # a new menu counts

    clock = fl_windows.time.monotonic()
    monkeypatch.setattr(fl_windows.time, "monotonic", lambda: clock + 60)
    assert fl_windows.popup_menu_count() == 2  # still there much later: it was left open


def test_enter_is_not_posted_without_exactly_one_menu(windows):
    assert fl_windows.press_enter_in_popup_menu() is False  # none of FL's is open

    _open_menu(windows, 301)
    _open_menu(windows, 302)
    assert fl_windows.press_enter_in_popup_menu() is False
    assert windows.posted == []


def test_open_menus_are_closed_innermost_first(windows):
    _open_menu(windows, 302)  # the parent: listed after its submenu
    windows.top = {301: windows.top[302], **windows.top}

    fl_windows.close_popup_menus()

    assert [(hwnd, key) for hwnd, message, key in windows.posted if message == KEY_DOWN] == [
        (301, ESCAPE), (302, ESCAPE)]


def test_every_visible_plugin_window_is_closed(windows):
    windows.children[210] = ("TPluginForm", "Sytrus (Master)", (0, 0, 9, 9), FL_EXE)
    windows.children[211] = ("TPluginForm", "Fruity Limiter (Insert 2)", (0, 0, 9, 9), FL_EXE)
    windows.children[212] = ("TPluginForm", "Hidden (Master)", (0, 0, 9, 9), FL_EXE)
    windows.hidden.add(212)

    assert fl_windows.close_all_plugin_windows() == 2
    assert windows.posted == [(210, CLOSE, 0), (211, CLOSE, 0)]


def test_no_plugin_window_is_closed_when_fl_is_not_running(windows):
    del windows.top[MAIN]

    assert fl_windows.close_all_plugin_windows() == 0


def test_a_minimized_fl_is_restored_behind_the_other_windows_without_focus(windows):
    assert fl_windows.restore_if_minimized() is False  # not minimized: left alone
    assert windows.shown == []

    windows.minimized = True
    assert fl_windows.restore_if_minimized() is True
    # shown without activating, then sent to the bottom: no move, no resize, no focus
    assert windows.shown == [(MAIN, 4), (MAIN, 1, 0x0001 | 0x0002 | 0x0010)]


def test_the_file_menu_is_opened_by_a_click_posted_to_the_menu_bar(windows):
    assert fl_windows.click_main_menu() is False  # no menu bar showing

    windows.children[220] = ("TNewMenu", "", (4, 4, 297, 37), FL_EXE)
    assert fl_windows.click_main_menu() is True
    assert windows.posted == [(220, 0x0201, 1), (220, 0x0202, 0)]
    assert windows.positions == [(16 << 16) | 12] * 2


def _open_dialog(windows: FakeWindows) -> None:
    windows.top[400] = ("#32770", "Save As", (0, 0, 9, 9), FL_EXE)
    windows.dialog_children[401] = ("Edit", "", (0, 0, 9, 9), FL_EXE)
    windows.dialog_children[402] = ("Button", "&Save", (0, 0, 9, 9), FL_EXE)


def test_a_file_name_is_set_in_the_dialog_and_its_default_button_pressed(windows):
    _open_dialog(windows)

    assert fl_windows.confirm_dialog_with_file_name("C:/out.wav") is True
    assert windows.texts == [(401, 0x000C, "C:/out.wav")]
    assert windows.posted == [(400, 0x0111, 1)]  # WM_COMMAND, IDOK


def test_a_dialog_is_only_filled_in_when_it_is_the_only_one_with_one_name_box(windows):
    assert fl_windows.confirm_dialog_with_file_name("C:/out.wav") is False  # none open

    _open_dialog(windows)
    windows.dialog_children[403] = ("Edit", "", (0, 0, 9, 9), FL_EXE)
    assert fl_windows.confirm_dialog_with_file_name("C:/out.wav") is False
    assert windows.texts == [] and windows.posted == []


def test_dialogs_are_cancelled_and_windows_closed_by_message(windows):
    _open_dialog(windows)
    windows.top[500] = ("TWAVRenderForm", "Rendering", (0, 0, 9, 9), FL_EXE)

    assert fl_windows.press_in_window("TWAVRenderForm", ENTER) is True
    fl_windows.cancel_dialogs()
    fl_windows.close_windows("TWAVRenderForm")

    assert windows.posted == [(500, KEY_DOWN, ENTER), (500, KEY_UP, ENTER),
                              (400, 0x0111, 2), (500, CLOSE, 0)]
    assert fl_windows.press_in_window("TFXForm", ENTER) is False  # no such window


def test_fls_message_box_is_found_by_its_title_and_closed_by_message(windows):
    assert fl_windows.open_message() is None
    assert fl_windows.close_messages() == 0

    windows.top[600] = ("TMsgForm", "FL Studio trial", (0, 0, 9, 9), FL_EXE)
    assert fl_windows.open_message() == "FL Studio trial"
    assert fl_windows.close_messages() == 1
    assert windows.posted == [(600, CLOSE, 0)]


def test_no_message_is_reported_when_windows_cannot_be_listed(monkeypatch):
    def unavailable():
        raise OSError("not on Windows")

    monkeypatch.setattr(win_focus, "_libraries", unavailable)

    assert fl_windows.open_message() is None


def test_the_pointer_is_over_a_menu_only_inside_an_open_one(windows):
    assert fl_windows.pointer_over_menu() is False  # no menu of FL's is open

    _open_menu(windows, 301)  # at (890, 326) to (1075, 662)
    assert fl_windows.pointer_over_menu() is False
    windows.pointer = (900, 400)
    assert fl_windows.pointer_over_menu() is True

    windows.pointer = None  # its position can't be read: assume the worst
    assert fl_windows.pointer_over_menu() is True
