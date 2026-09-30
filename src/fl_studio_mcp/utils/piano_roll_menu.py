"""Running a piano roll script from the piano roll's menu, by mouse and keyboard.

FL's "run last script again" shortcut (Ctrl+Alt+Y) only works once a script has
been run from the menu in that FL session. This does that first run: menu arrow,
Tools, then the script in the list at the end of that submenu. Windows only.

Menu items can't be read from outside FL, so the script is reached by position:
Up from the top of the Tools submenu wraps to its last entry, and the scripts
are listed last, sorted by name. Every step is checked by counting FL's open
popup menus, so keys never land in a dialog or another panel.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.fl_trigger import get_trigger, keyboard_lock
from fl_studio_mcp.utils.paths import get_piano_roll_scripts_dir

logger = logging.getLogger(__name__)

SCRIPT_NAME = "ComposeWithLLM"
SCRIPT_SUFFIX = ".pyscript"
# Where FL keeps its own piano roll scripts, below its install folder.
STOCK_SCRIPTS_DIR = Path("System") / "Config" / "Piano roll scripts"
TOOLS_MENU_KEY = "t"

MENU_TIMEOUT = 1.5  # seconds for a popup menu to open or close
MENU_POLL_INTERVAL = 0.05
KEY_DELAY = 0.05


def _script_names(folder: Path) -> set[str] | None:
    """Script names in a folder; None if it has sub-folders of scripts.

    FL shows a sub-folder as a submenu, and where that sits in the list isn't
    known, so the script's position can't be worked out then.
    """
    if not folder.is_dir():
        return set()
    entries = list(folder.iterdir())
    if any(entry.is_dir() and any(entry.glob(f"*{SCRIPT_SUFFIX}")) for entry in entries):
        return None
    return {path.stem for path in entries if path.suffix.lower() == SCRIPT_SUFFIX}


def scripts_after(name: str, folders: list[Path]) -> int | None:
    """How many scripts FL lists after `name` (sorted by name).

    None if the script isn't listed or the list's order can't be known.
    """
    per_folder = [_script_names(folder) for folder in folders]
    if any(found is None for found in per_folder):
        return None
    names = sorted(set().union(*per_folder), key=str.lower)
    if name not in names:
        return None
    return len(names) - 1 - names.index(name)


def _script_folders() -> list[Path]:
    folders = [get_piano_roll_scripts_dir()]
    install_dir = fl_windows.fl_install_dir()
    if install_dir is not None:
        folders.append(install_dir / STOCK_SCRIPTS_DIR)
    return folders


def _devices() -> tuple[Any, Any, Any, Any]:
    """pynput's keyboard, Key enum, mouse and left button (imported late: needs a desktop)."""
    from pynput.keyboard import Controller, Key
    from pynput.mouse import Button
    from pynput.mouse import Controller as MouseController

    return Controller(), Key, MouseController(), Button.left


def _wait_for_menus(count: int) -> bool:
    """Wait until FL has exactly `count` popup menus open."""
    deadline = time.monotonic() + MENU_TIMEOUT
    while True:
        if fl_windows.popup_menu_count() == count:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(MENU_POLL_INTERVAL)


def _wait_for_arrow() -> tuple[int, int] | None:
    """The piano roll's menu arrow once it can be clicked, or None.

    Seen live: right after FL is brought to the front (or the piano roll is
    re-shown), Windows still reports the window that was on top for a moment.
    If it stays covered (FL sometimes re-shows the piano roll partly under the
    docked browser), the piano roll is moved into the open once.
    """
    arrow = _poll_for_arrow()
    if arrow is None and fl_windows.move_piano_roll_into_view():
        arrow = _poll_for_arrow()
    return arrow


def _poll_for_arrow() -> tuple[int, int] | None:
    deadline = time.monotonic() + MENU_TIMEOUT
    while True:
        arrow = fl_windows.piano_roll_menu_arrow()
        if arrow is not None or time.monotonic() >= deadline:
            return arrow
        time.sleep(MENU_POLL_INTERVAL)


def _close_menus(keyboard: Any, key: Any) -> None:
    for _ in range(fl_windows.popup_menu_count()):
        keyboard.tap(key.esc)
        time.sleep(KEY_DELAY)


def run_script_from_menu(name: str = SCRIPT_NAME) -> bool:
    """Run a piano roll script through the piano roll's Tools menu.

    The piano roll must be showing. Returns True if the menu was walked to the
    script and closed again; the caller confirms the script ran from its output.
    """
    if platform.system() != "Windows":
        return False
    try:
        with keyboard_lock:
            return _run_script_from_menu(name)
    except Exception:
        logger.exception("Running %s from the piano roll menu failed", name)
        return False


def _run_script_from_menu(name: str) -> bool:
    after = scripts_after(name, _script_folders())
    if after is None or not get_trigger().focus():
        return False
    arrow = _wait_for_arrow()
    if arrow is None or fl_windows.popup_menu_count() != 0:
        return False

    keyboard, key, mouse, left_button = _devices()
    try:
        mouse.position = arrow
        time.sleep(KEY_DELAY)
        mouse.click(left_button)
        if not _wait_for_menus(1):
            return False
        keyboard.tap(TOOLS_MENU_KEY)
        if not _wait_for_menus(2):
            return False

        keyboard.tap(key.right)  # into the submenu, on its first entry
        for _ in range(after + 1):  # Up wraps to the last entry; scripts are listed last
            time.sleep(KEY_DELAY)
            keyboard.tap(key.up)
        time.sleep(KEY_DELAY)
        # Still exactly the menu and its Tools submenu: the walk didn't open or close one.
        if fl_windows.popup_menu_count() != 2:
            return False
        keyboard.tap(key.enter)
        return _wait_for_menus(0)
    finally:
        _close_menus(keyboard, key)  # no menu may be left open, whatever happened
