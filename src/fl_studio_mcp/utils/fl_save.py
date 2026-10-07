"""Saving the open project through FL Studio's File menu.

Seen live (FL 2026): `transport.globalTransport(midi.FPT_Save)` (Ctrl+S) saved the
project twice and then, after FL had reloaded it, did nothing at all (no window,
no file), while File > Save did save. So this posts the menu walk: a click on
FILE in the menu bar, Home, three Downs (New, New from template, Open, Save; the
separators are skipped) and Enter, like the export (`fl_render`). The save is
confirmed by the project's file on disk (seen live: written about 7 s after
Enter). Seen live: Save doesn't rewrite a project that hasn't changed since its
last save. Windows only.
"""

from __future__ import annotations

import platform
import time
from pathlib import Path

from fl_studio_mcp.utils import fl_windows

MENU_CLICKS = 3
MENU_CLICK_TIMEOUT = 0.5  # seconds the File menu gets to open after a click
SAVE_DOWNS = 3  # File menu: New, New from template, Open, Save
SAVE_TIMEOUT = 20.0  # seconds for the file to be written
POLL_INTERVAL = 0.1
# FL 2026's trial shows a "Buy FL Studio" window first and saves once it is closed.
TRIAL_SHOP_CLASS = "TInAppShopForm"


class SaveError(Exception):
    """The project could not be saved; the message says why."""


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if done():
            return True
        time.sleep(POLL_INTERVAL)
    return done()


def _choose_save() -> None:
    """Open FILE in the menu bar and choose Save."""
    for _ in range(MENU_CLICKS):
        if not fl_windows.click_main_menu():
            raise SaveError("FL Studio's File menu did not open")
        if _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_CLICK_TIMEOUT):
            break
    else:
        raise SaveError("FL Studio's File menu did not open")
    for key in [fl_windows.VK_HOME] + [fl_windows.VK_DOWN] * SAVE_DOWNS:
        if not fl_windows.press_in_popup_menu(key, menus=1):
            raise SaveError("FL Studio's File menu closed unexpectedly")
    if fl_windows.pointer_over_menu():
        fl_windows.close_popup_menus()
        raise SaveError(
            "The mouse pointer is over FL Studio's menu, where it moves the highlight, so "
            "nothing was chosen. Try again with the pointer elsewhere."
        )
    if not fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=1):
        raise SaveError("FL Studio's File menu closed unexpectedly")


def save_project(project_file: Path | None) -> bool:
    """Choose File > Save; returns once `project_file` has been written.

    A project with no file yet makes FL open its Save As dialog, which is left
    for the user and reported as an error. Returns whether the trial's "Buy FL
    Studio" window came (it is closed). Raises SaveError.
    """
    if platform.system() != "Windows":
        raise SaveError("Saving through FL Studio's menu works on Windows only")
    message = fl_windows.open_message()
    if message is not None:
        raise SaveError(f"FL Studio is showing a message box ({message!r})")
    if fl_windows.popup_menu_count() or fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS):
        raise SaveError("a menu or dialog is open in FL Studio")
    before = project_file.stat().st_mtime if project_file and project_file.exists() else None
    _choose_save()
    shop = False

    def written() -> bool:
        nonlocal shop
        if fl_windows.window_count(TRIAL_SHOP_CLASS):
            fl_windows.close_windows(TRIAL_SHOP_CLASS)
            shop = True
        if fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS):
            raise SaveError(
                "FL Studio opened its Save As dialog (the project has no file yet); "
                "answer it in FL Studio"
            )
        return (project_file is not None and project_file.exists()
                and project_file.stat().st_mtime != before)

    if not _wait_until(written, SAVE_TIMEOUT):
        raise SaveError(
            f"File > Save was chosen, but {project_file} was not written within "
            f"{SAVE_TIMEOUT:g} s. FL Studio doesn't rewrite a project that hasn't changed "
            "since its last save (seen live), so it may already be saved"
        )
    return shop
