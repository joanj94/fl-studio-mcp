"""Looking inside FL Studio's window on Windows: its panels, popup menus and install folder.

FL's panels (piano roll, channel rack, ...) are child windows with a caption
child carrying the panel's title, and every open popup menu is a top-level
window of its own. That lets keystroke sequences find their target and check
each step. Seen in FL Studio 2026; Windows only.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils import win_focus

CAPTION_CLASS = "TNewCaption"
POPUP_MENU_CLASS = "TQuickPopupMenuWindow"
RENDER_WINDOW_CLASS = "TWAVRenderForm"
# Windows' standard dialog class: FL's file dialogs (the title depends on the language).
SYSTEM_DIALOG_CLASS = "#32770"
PIANO_ROLL_TITLE = "Piano roll"
# The menu arrow sits at the caption's left edge (pixels from its top-left corner).
MENU_ARROW_OFFSET = (11, 11)
# Where a panel is put (in its workspace's coordinates) when it has to be moved into view.
PANEL_HOME = (40, 40)

_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010

Rect = tuple[int, int, int, int]  # left, top, right, bottom


def _class_name(user32: Any, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buffer, 256)
    return buffer.value


def _rect(user32: Any, hwnd: int) -> Rect:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _enumerate(enumerator: Any, *parent: int) -> list[int]:
    """Window handles passed to an EnumWindows-style callback."""
    found: list[int] = []

    def visit(hwnd: int, _lparam: int) -> bool:
        found.append(hwnd)
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    enumerator(*parent, callback_type(visit), 0)
    return found


def panel_caption(user32: Any, main: int, title: str) -> tuple[int, Rect] | None:
    """Handle and screen rectangle of the caption bar of the FL panel titled `title...`."""
    for hwnd in _enumerate(user32.EnumChildWindows, main):
        if (
            user32.IsWindowVisible(hwnd)
            and _class_name(user32, hwnd) == CAPTION_CLASS
            and win_focus._window_title(user32, hwnd).startswith(title)
        ):
            return hwnd, _rect(user32, hwnd)
    return None


def panel_menu_arrow(user32: Any, main: int, title: str) -> tuple[int, int] | None:
    """Screen position of a panel's menu arrow, or None if it isn't there to be clicked.

    A caption can be "visible" yet covered by another window (a floating plugin,
    another panel), so the window actually under the point must be the caption.
    """
    found = panel_caption(user32, main, title)
    if found is None:
        return None
    hwnd, rect = found
    x, y = rect[0] + MENU_ARROW_OFFSET[0], rect[1] + MENU_ARROW_OFFSET[1]
    if user32.WindowFromPoint(wintypes.POINT(x, y)) != hwnd:
        return None
    return x, y


def move_panel_into_view(user32: Any, main: int, title: str) -> bool:
    """Move an FL panel to the top-left of FL's workspace, keeping its size.

    Seen live: FL can re-show a panel partly underneath a docked panel (the
    browser), where its menu arrow can't be clicked.
    """
    found = panel_caption(user32, main, title)
    if found is None:
        return False
    panel = user32.GetParent(found[0])
    if not panel:
        return False
    flags = _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE
    return bool(user32.SetWindowPos(panel, None, *PANEL_HOME, 0, 0, flags))


def top_level_windows(user32: Any, kernel32: Any, class_name: str) -> list[Rect]:
    """Rectangles of FL's visible top-level windows of one window class."""
    return [
        _rect(user32, hwnd)
        for hwnd in _enumerate(user32.EnumWindows)
        if user32.IsWindowVisible(hwnd)
        and _class_name(user32, hwnd) == class_name
        and win_focus._owned_by_fl(user32, kernel32, hwnd)
    ]


def popup_menus(user32: Any, kernel32: Any) -> list[Rect]:
    """Rectangles of FL's open popup menus (a submenu is a menu of its own)."""
    return top_level_windows(user32, kernel32, POPUP_MENU_CLASS)


def piano_roll_menu_arrow() -> tuple[int, int] | None:
    """Screen position of the piano roll's menu arrow, or None if it can't be clicked.

    None when no piano roll is showing or something covers its caption.
    """
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return None
    return panel_menu_arrow(user32, main, PIANO_ROLL_TITLE)


def move_piano_roll_into_view() -> bool:
    """Put the piano roll where nothing docked covers it. False if it isn't showing."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and move_panel_into_view(user32, main, PIANO_ROLL_TITLE)


def popup_menu_count() -> int:
    """How many popup menus FL Studio has open right now."""
    return len(popup_menus(*win_focus._libraries()))


def window_count(class_name: str) -> int:
    """How many visible top-level windows of a class FL Studio has (dialogs, render window)."""
    return len(top_level_windows(*win_focus._libraries(), class_name))


def fl_install_dir() -> Path | None:
    """Folder FL Studio runs from (it holds the stock scripts), or None if FL isn't running."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return None
    path = win_focus._window_exe_path(user32, kernel32, main)
    return Path(path).parent if path else None
