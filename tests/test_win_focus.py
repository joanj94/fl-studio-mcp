"""Finding and focusing FL Studio's window on Windows, against a fake user32/kernel32."""

from __future__ import annotations

import sys

import pytest

from fl_studio_mcp.utils import win_focus

FL = 100
BROWSER = 200
FL_PICKER = 300


class FakeWindows:
    """user32 and kernel32 in one: a few windows, each owned by a program."""

    def __init__(self) -> None:
        self.titles = {BROWSER: "FL Studio manual - Browser", FL: "FL Studio 2026", FL_PICKER: ""}
        self.exes = {
            BROWSER: r"C:\Apps\browser.exe",
            FL: r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe",
            FL_PICKER: r"C:\Program Files\Image-Line\FL Studio 2026\FL64.exe",
        }
        self.hidden: set[int] = set()
        self.foreground = BROWSER
        self.refuses_focus = False
        self.closed: list[int] = []

    # user32
    def IsWindowVisible(self, hwnd):  # noqa: N802 - Windows API name
        return hwnd not in self.hidden

    def GetWindowTextLengthW(self, hwnd):  # noqa: N802
        return len(self.titles[hwnd])

    def GetWindowTextW(self, hwnd, buffer, size):  # noqa: N802
        buffer.value = self.titles[hwnd]

    def GetWindowThreadProcessId(self, hwnd, pid_ref):  # noqa: N802
        pid_ref._obj.value = hwnd  # one process per window keeps the fake simple

    def EnumWindows(self, callback, lparam):  # noqa: N802
        for hwnd in self.titles:
            if not callback(hwnd, lparam):
                break

    def ShowWindow(self, hwnd, command):  # noqa: N802
        self.hidden.discard(hwnd)

    def keybd_event(self, *args):
        pass

    def SetForegroundWindow(self, hwnd):  # noqa: N802
        if not self.refuses_focus:
            self.foreground = hwnd

    def GetForegroundWindow(self):  # noqa: N802
        return self.foreground

    # kernel32
    def OpenProcess(self, access, inherit, pid):  # noqa: N802
        return pid if pid in self.exes else 0

    def QueryFullProcessImageNameW(self, process, flags, buffer, size_ref):  # noqa: N802
        buffer.value = self.exes[process]
        return 1

    def CloseHandle(self, process):  # noqa: N802
        self.closed.append(process)


@pytest.fixture
def windows(monkeypatch) -> FakeWindows:
    monkeypatch.setattr(win_focus.time, "sleep", lambda seconds: None)
    return FakeWindows()


def test_fl_window_needs_the_title_and_the_program(windows):
    assert win_focus.is_fl_window(windows, windows, FL) is True
    assert win_focus.is_fl_window(windows, windows, BROWSER) is False  # title only
    assert win_focus.is_fl_window(windows, windows, FL_PICKER) is False  # program only
    assert windows.closed  # process handles are released


def test_hidden_or_unreadable_windows_are_not_fl(windows):
    windows.hidden.add(FL)
    assert win_focus.is_fl_window(windows, windows, FL) is False

    windows.hidden.clear()
    del windows.exes[FL]
    assert win_focus.is_fl_window(windows, windows, FL) is False


@pytest.mark.skipif(sys.platform != "win32", reason="EnumWindows callbacks need Windows ctypes")
def test_find_fl_window_skips_lookalikes(windows):
    assert win_focus.find_fl_window(windows, windows) == FL

    windows.hidden.add(FL)
    assert win_focus.find_fl_window(windows, windows) is None


def test_focus_window_confirms_the_foreground_window(windows):
    assert win_focus.focus_window(windows, FL) is True
    assert windows.foreground == FL


def test_focus_window_fails_when_windows_refuses(windows):
    windows.refuses_focus = True

    assert win_focus.focus_window(windows, FL) is False


def test_has_focus_accepts_any_window_of_fl(windows):
    assert win_focus.fl_studio_has_focus(windows, windows) is False  # the browser

    windows.foreground = FL_PICKER
    assert win_focus.fl_studio_has_focus(windows, windows) is True

    windows.foreground = 0
    assert win_focus.fl_studio_has_focus(windows, windows) is False
