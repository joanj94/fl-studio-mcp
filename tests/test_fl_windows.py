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
        self.covering: int | None = None  # a window lying over everything else
        self.moved: list[tuple] = []

    def GetParent(self, hwnd):  # noqa: N802 - Windows API name
        return {203: 202}.get(hwnd, 0)

    def SetWindowPos(self, hwnd, after, x, y, width, height, flags):  # noqa: N802
        self.moved.append((hwnd, x, y, flags))
        return 1

    def WindowFromPoint(self, point):  # noqa: N802 - Windows API name
        if self.covering is not None:
            return self.covering
        for hwnd, (_, _, (left, top, right, bottom), _) in reversed(self.children.items()):
            if left <= point.x < right and top <= point.y < bottom and hwnd not in self.hidden:
                return hwnd
        return MAIN

    def _window(self, hwnd):
        return {**self.top, **self.children}[hwnd]

    def EnumWindows(self, callback, lparam):  # noqa: N802 - Windows API name
        for hwnd in self.top:
            callback(hwnd, lparam)

    def EnumChildWindows(self, parent, callback, lparam):  # noqa: N802
        assert parent == MAIN
        for hwnd in self.children:
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
    return fake


def _open_menu(windows: FakeWindows, hwnd: int) -> None:
    windows.top[hwnd] = ("TQuickPopupMenuWindow", "", (890, 326, 1075, 662), FL_EXE)


def test_panel_caption_finds_the_caption_not_the_panel(windows):
    assert fl_windows.panel_caption(windows, MAIN, "Piano roll") == (203, (890, 303, 1840, 326))
    assert fl_windows.panel_caption(windows, MAIN, "Mixer") is None


def test_piano_roll_menu_arrow_is_at_the_captions_left_end(windows):
    assert fl_windows.piano_roll_menu_arrow() == (901, 314)


def test_piano_roll_menu_arrow_needs_a_visible_piano_roll(windows):
    windows.hidden.add(203)

    assert fl_windows.piano_roll_menu_arrow() is None


def test_piano_roll_menu_arrow_is_not_offered_when_something_covers_it(windows):
    windows.covering = 999  # e.g. a floating plugin window over the caption

    assert fl_windows.piano_roll_menu_arrow() is None


def test_a_covered_piano_roll_can_be_moved_into_view_keeping_its_size(windows):
    assert fl_windows.move_piano_roll_into_view() is True
    # the panel (the caption's parent) is moved; flags: no resize, no z-order change, no focus
    assert windows.moved == [(202, 40, 40, 0x0001 | 0x0004 | 0x0010)]

    assert fl_windows.move_panel_into_view(windows, MAIN, "Mixer") is False  # not showing
    assert fl_windows.move_panel_into_view(windows, MAIN, "Channel rack") is False  # no parent


def test_nothing_is_found_when_fl_is_not_running(windows):
    del windows.top[MAIN]

    assert fl_windows.piano_roll_menu_arrow() is None
    assert fl_windows.move_piano_roll_into_view() is False
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
