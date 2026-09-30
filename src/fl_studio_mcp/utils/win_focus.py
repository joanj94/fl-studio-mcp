"""Bringing FL Studio's window to the foreground on Windows (ctypes only).

Keystrokes go to whichever window has focus, so a caller must not send any
unless `focus_fl_studio` returned True. `user32`/`kernel32` are parameters so
the logic can be tested without Windows.
"""

from __future__ import annotations

import ctypes
import time
from typing import Any

FL_TITLE = "FL Studio"
# FL64.exe, FL.exe, "FL64 (scaled).exe"
FL_EXE_PREFIX = "fl"

FOREGROUND_CHECKS = 10
FOREGROUND_CHECK_DELAY = 0.05  # seconds

_SW_RESTORE = 9
_VK_MENU = 0x12
_KEYEVENTF_KEYUP = 0x0002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_MAX_PATH = 260


def _window_title(user32: Any, hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length == 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _window_exe_name(user32: Any, kernel32: Any, hwnd: int) -> str:
    """File name of the program owning a window, lowercase; "" if it can't be read."""
    return _window_exe_path(user32, kernel32, hwnd).rsplit("\\", 1)[-1].lower()


def _window_exe_path(user32: Any, kernel32: Any, hwnd: int) -> str:
    """Full path of the program owning a window; "" if it can't be read."""
    pid = ctypes.c_ulong(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not process:
        return ""
    try:
        buffer = ctypes.create_unicode_buffer(_MAX_PATH)
        size = ctypes.c_ulong(_MAX_PATH)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return ""
        return buffer.value.replace("/", "\\")
    finally:
        kernel32.CloseHandle(process)


def _owned_by_fl(user32: Any, kernel32: Any, hwnd: int) -> bool:
    exe = _window_exe_name(user32, kernel32, hwnd)
    return exe.startswith(FL_EXE_PREFIX) and exe.endswith(".exe")


def is_fl_window(user32: Any, kernel32: Any, hwnd: int) -> bool:
    """True for a visible window titled "...FL Studio..." that FL's own program owns.

    The title alone isn't enough: a browser tab or an Explorer folder can carry it.
    """
    if not user32.IsWindowVisible(hwnd) or FL_TITLE not in _window_title(user32, hwnd):
        return False
    return _owned_by_fl(user32, kernel32, hwnd)


def find_fl_window(user32: Any, kernel32: Any) -> int | None:
    """Handle of FL Studio's main window, or None."""
    found: list[int] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        if is_fl_window(user32, kernel32, hwnd):
            found.append(hwnd)
            return False  # stop enumerating
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(callback_type(visit), 0)
    return found[0] if found else None


def focus_window(user32: Any, hwnd: int) -> bool:
    """Foreground a window and confirm Windows really did it."""
    user32.ShowWindow(hwnd, _SW_RESTORE)  # restore if minimized
    # Windows forbids a background process from taking focus; an ALT tap lifts that.
    user32.keybd_event(_VK_MENU, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    user32.keybd_event(_VK_MENU, 0, _KEYEVENTF_KEYUP, 0)
    for _ in range(FOREGROUND_CHECKS):
        if user32.GetForegroundWindow() == hwnd:
            return True
        time.sleep(FOREGROUND_CHECK_DELAY)
    return False


def fl_studio_has_focus(user32: Any, kernel32: Any) -> bool:
    """True if the foreground window belongs to FL Studio right now.

    Any window of FL's counts (the plugin picker and dialogs have other titles).
    """
    hwnd = user32.GetForegroundWindow()
    return bool(hwnd) and _owned_by_fl(user32, kernel32, hwnd)


def _libraries() -> tuple[Any, Any]:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    # Handles are pointer-sized; ctypes would truncate them to int otherwise.
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.WindowFromPoint.restype = ctypes.c_void_p
    user32.GetParent.restype = ctypes.c_void_p
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong),
    ]
    return user32, kernel32


def focus_fl_studio() -> bool:
    """Bring FL Studio to the foreground. True only if it now has focus."""
    user32, kernel32 = _libraries()
    hwnd = find_fl_window(user32, kernel32)
    return hwnd is not None and focus_window(user32, hwnd)


def fl_studio_is_focused() -> bool:
    """True if FL Studio is the foreground window."""
    return fl_studio_has_focus(*_libraries())
