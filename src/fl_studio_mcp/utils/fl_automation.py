"""Making FL Studio create an automation clip for a parameter.

FL's API can't create automation, but FL's ADD menu has "Automation for last
tweaked parameter" (second from the bottom, above "Pattern"), and a parameter
changed through the API counts as tweaked (seen live: setting a mixer track's
volume or pan to the value it already had, then that entry, gave a channel
"<track> - Volume" / "<track> - Panning" linked to it, its points a flat line
at the current value). FL also puts one clip of it into the playlist: from the
song position to the song's end, on an empty playlist track of its choosing
(seen: End, 15, 19 and 21, not always the first empty one; once in eight runs
no clip at all, unexplained). Loading a preset into the clip's channel makes that clip
as long as the preset's points.

The menu is reached like File > Export (`utils/fl_render.py`): a posted click
on FILE in the menu bar opens the File menu, and Right on an entry without a
submenu moves on to the next menu of the bar: EDIT, then ADD. Each Right is
checked by the open menu moving right. Windows only.
"""

from __future__ import annotations

import platform
import time

from fl_studio_mcp.utils import fl_windows

MENU_TIMEOUT = 1.0  # seconds for a menu to open or change
MENU_CLICKS = 3  # seen live: the first click on FILE after a menu closed may open nothing
POLL_INTERVAL = 0.05
MENUS_RIGHT_OF_FILE = 2  # FILE, EDIT, ADD
# In the ADD menu, from its last entry ("Pattern") up to the one wanted.
UPS_FROM_END = 1

Rect = tuple[int, int, int, int]


class AutomationError(Exception):
    """FL's menu could not be used to create the automation clip."""


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def _one_menu() -> Rect | None:
    rects = fl_windows.popup_menu_rects()
    return rects[0] if len(rects) == 1 else None


def _open_file_menu() -> Rect:
    if fl_windows.popup_menu_count():
        raise AutomationError("FL Studio has a menu open. Close it and try again.")
    for _ in range(MENU_CLICKS):
        if not fl_windows.click_main_menu():
            raise AutomationError("FL Studio's menu bar was not found.")
        if _wait_until(lambda: _one_menu() is not None, MENU_TIMEOUT):
            return _one_menu()  # type: ignore[return-value]
    raise AutomationError("FL Studio's File menu did not open.")


def _move_right(before: Rect) -> Rect:
    """Right in a menu of the bar: the next menu must open, further right."""
    if not fl_windows.press_in_popup_menu(fl_windows.VK_RIGHT, menus=1):
        raise AutomationError("FL Studio's menu closed unexpectedly.")
    moved = _wait_until(
        lambda: (rect := _one_menu()) is not None and rect[0] > before[0], MENU_TIMEOUT
    )
    if not moved:
        # A submenu opened (an entry was highlighted under the mouse pointer) or nothing moved.
        raise AutomationError(
            "FL Studio's menu bar did not move on to the next menu; is the mouse pointer "
            "over FL's menu?"
        )
    return _one_menu()  # type: ignore[return-value]


def choose_automation_for_last_tweaked() -> None:
    """ADD > "Automation for last tweaked parameter". The caller checks what FL made."""
    if platform.system() != "Windows":
        raise AutomationError("Creating automation clips is only supported on Windows.")
    try:
        menu = _open_file_menu()
        for _ in range(MENUS_RIGHT_OF_FILE):
            menu = _move_right(menu)
        for key in [fl_windows.VK_END] + [fl_windows.VK_UP] * UPS_FROM_END:
            if not fl_windows.press_in_popup_menu(key, menus=1):
                raise AutomationError("FL Studio's ADD menu closed unexpectedly.")
        if fl_windows.pointer_over_menu():
            raise AutomationError(
                "The mouse pointer is over FL Studio's menu, where it moves the highlight, "
                "so nothing was chosen. Try again with the pointer elsewhere."
            )
        if not fl_windows.press_enter_in_popup_menu():
            raise AutomationError("FL Studio's ADD menu did not take the choice.")
    finally:
        if fl_windows.popup_menu_count():
            fl_windows.close_popup_menus()
