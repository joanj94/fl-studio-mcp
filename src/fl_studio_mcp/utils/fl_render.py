"""Rendering the project to a WAV file through FL Studio's own export dialog.

The scripting API can't export audio, so this walks File > Export > Wave file,
fills in the Save dialog and starts the render window. Every click and key is
posted to FL's own windows (`fl_windows`), so FL stays in the background and
nothing is typed into another program. Each window is waited for before the
next step, and the render is done when the render window has closed and the
file exists. Windows only.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.fl_trigger import keyboard_lock

logger = logging.getLogger(__name__)

WINDOW_TIMEOUT = 5.0  # seconds for a menu or dialog to open
POLL_INTERVAL = 0.05
MENU_CLICKS = 3
MENU_CLICK_TIMEOUT = 0.5  # seconds the File menu gets to open after a click
SUBMENU_TIMEOUT = 1.0  # seconds for Right to open a submenu or move on to the next menu
# End goes to the File menu's last entry, Exit; above it are Revert to last backup,
# the recent projects (none until the project has been saved; seen live: one entry
# after the first save) and then Export. None of those has a submenu, so Export is
# the first entry from the end where Right opens one. "Wave file..." is the Export
# submenu's first entry (Home). Home and End work from wherever the highlight is:
# the mouse pointer moves it.
MAX_UPS_FROM_EXIT_TO_EXPORT = 24
# The Export submenu starts with "Wave file...", "MP3 file...": entries down from the first.
EXPORT_ENTRIES = {".wav": 0, ".mp3": 1}
RESTORE_DELAY = 0.5  # seconds FL gets to draw its window after being un-minimized


class RenderError(Exception):
    """The render could not be started or did not finish; the message says why."""


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def _wait_for(class_name: str, count: int, timeout: float) -> bool:
    """Wait until FL has exactly `count` windows of a class."""
    return _wait_until(lambda: fl_windows.window_count(class_name) == count, timeout)


def _wait_for_menus(count: int) -> bool:
    return _wait_until(lambda: fl_windows.popup_menu_count() == count, WINDOW_TIMEOUT)


def _busy() -> str | None:
    """What is in the way of starting a render, or None."""
    message = fl_windows.open_message()
    if message is not None:
        return f"FL Studio is showing a message box ({message!r}); fl_close_message closes it"
    if fl_windows.popup_menu_count():
        return "a menu is open in FL Studio"
    if fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS):
        return "a dialog is open in FL Studio"
    if fl_windows.window_count(fl_windows.RENDER_WINDOW_CLASS):
        return "FL Studio's render window is already open"
    return None


def _submenu_opened_low() -> bool:
    """True if the open submenu starts in the File menu's lower half, where Export is.

    The entries can't be read. Seen live: the submenus near the top of the File
    menu are the ones that open other projects, and a submenu opens level with
    its entry.
    """
    menus = fl_windows.popup_menu_rects()
    if len(menus) != 2:
        return False
    (_, top, _, bottom), (_, submenu_top, _, _) = menus
    return submenu_top > (top + bottom) / 2


def _opens_submenu(ups: int) -> bool:
    """Open the File menu, go `ups` entries up from Exit and press Right: did a submenu open?

    On an entry without a submenu, Right moves on to the menu bar's next menu: still
    one menu open, but another one, which is how the miss is seen at once. The menu
    is then closed.
    """
    # Seen live: after a menu opened from the bar was closed, the next click on FILE
    # sometimes opens nothing; the one after it does.
    for _ in range(MENU_CLICKS):
        if not fl_windows.click_main_menu():
            raise RenderError("FL Studio's File menu did not open")
        if _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_CLICK_TIMEOUT):
            break
    else:
        raise RenderError("FL Studio's File menu did not open")
    file_menu = fl_windows.popup_menu_rects()
    for key in [fl_windows.VK_END] + [fl_windows.VK_UP] * ups + [fl_windows.VK_RIGHT]:
        if not fl_windows.press_in_popup_menu(key, menus=1):
            raise RenderError("FL Studio's File menu closed unexpectedly")
    _wait_until(
        lambda: fl_windows.popup_menu_count() != 1 or fl_windows.popup_menu_rects() != file_menu,
        SUBMENU_TIMEOUT,
    )
    if fl_windows.popup_menu_count() == 2:
        return True
    fl_windows.close_popup_menus()
    if not _wait_for_menus(0):
        raise RenderError("FL Studio's menu did not close")
    return False


def _open_save_dialog(entry: int) -> None:
    """File > Export > the entry that many below the first: leaves FL's Save dialog open."""
    if not any(_opens_submenu(ups) for ups in range(1, MAX_UPS_FROM_EXIT_TO_EXPORT + 1)):
        raise RenderError("The Export submenu was not found in FL Studio's File menu")
    if not _submenu_opened_low():
        raise RenderError(
            "The submenu that opened is not where the File menu's Export entry is, so nothing "
            "was chosen in it"
        )
    for key in [fl_windows.VK_HOME] + [fl_windows.VK_DOWN] * entry:
        if not fl_windows.press_in_popup_menu(key, menus=2):
            raise RenderError("FL Studio's Export menu closed unexpectedly")
    if fl_windows.pointer_over_menu():
        raise RenderError(
            "The mouse pointer is over FL Studio's menu, where it moves the highlight, so "
            "the export was not started. Try again with the pointer elsewhere."
        )
    if not fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=2):
        raise RenderError("FL Studio's Export menu closed unexpectedly")
    if not _wait_for(fl_windows.SYSTEM_DIALOG_CLASS, 1, WINDOW_TIMEOUT):
        raise RenderError("FL Studio's export dialog did not open")


def _clean_up() -> None:
    """Close whatever the export left open, so FL isn't left blocked."""
    fl_windows.close_popup_menus()
    fl_windows.cancel_dialogs()
    fl_windows.close_windows(fl_windows.RENDER_WINDOW_CLASS)


def render_audio(path: Path, timeout: float) -> float:
    """Render what FL would play (pattern or song, per its mode) to `path`.

    The file's suffix picks the format (.wav or .mp3). `path` must not exist
    yet, so FL never asks to overwrite. Returns the seconds the render took.
    Raises RenderError with the reason otherwise.
    """
    if platform.system() != "Windows":
        raise RenderError("Rendering is only supported on Windows")
    entry = EXPORT_ENTRIES.get(path.suffix.lower())
    if entry is None:
        raise RenderError(f"The render path must end in .wav or .mp3, got {path.name!r}")
    if path.exists():
        raise RenderError(f"{path} already exists; pick another file name")
    if not path.parent.is_dir():
        raise RenderError(f"The folder {path.parent} does not exist")

    with keyboard_lock:
        rendering = False
        try:
            if fl_windows.restore_if_minimized():
                time.sleep(RESTORE_DELAY)
            busy = _busy()
            if busy:
                raise RenderError(f"Cannot start a render while {busy}")
            try:
                _open_save_dialog(entry)
                # The dialog's window shows up before its file name box does.
                if not _wait_until(
                    lambda: fl_windows.confirm_dialog_with_file_name(str(path)), WINDOW_TIMEOUT
                ):
                    raise RenderError("The file name could not be put into FL Studio's dialog")
                if not _wait_for(fl_windows.RENDER_WINDOW_CLASS, 1, WINDOW_TIMEOUT):
                    raise RenderError(
                        "FL Studio's render window did not open (is the path one FL can write to?)"
                    )
                started = time.monotonic()
                if not fl_windows.press_in_window(
                    fl_windows.RENDER_WINDOW_CLASS, fl_windows.VK_RETURN
                ):
                    raise RenderError("FL Studio's render window closed unexpectedly")
                rendering = True
                if not _wait_for(fl_windows.RENDER_WINDOW_CLASS, 0, timeout):
                    raise RenderError(f"The render did not finish within {timeout:.0f} s")
            except Exception:
                if not rendering:  # a render that is only slow is left to finish
                    try:
                        _clean_up()
                    except Exception:  # the failure being reported matters more
                        logger.exception("Could not close what the export left open")
                raise
        except RenderError:
            raise
        except Exception as e:
            raise RenderError(f"Could not drive FL Studio's export dialog: {e}") from e

    if not path.is_file():
        raise RenderError(f"FL Studio closed its render window but {path} was not written")
    return time.monotonic() - started
