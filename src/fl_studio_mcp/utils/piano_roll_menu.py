"""Running a piano roll script from the piano roll's menu, without taking the focus.

FL's "run last script again" shortcut (Ctrl+Alt+Y) needs FL in front and only
works once a script has been run from the menu in that FL session. This walks
the menu instead: menu arrow, Tools, then the script in the list at the end of
that submenu. The click and the keys are posted to FL's own windows, so FL can
stay in the background and nothing is typed into another program. Windows only.

Menu items can't be read from outside FL, so the script is reached by position:
End goes to the Tools submenu's last entry, and the scripts are listed last,
sorted by name. Every step is checked by counting FL's open popup menus, and
Enter is only pressed if the mouse pointer isn't over the menu, where it would
have moved the highlight.
"""

from __future__ import annotations

import logging
import platform
import time
from pathlib import Path

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.paths import get_piano_roll_scripts_dir

logger = logging.getLogger(__name__)

SCRIPT_NAME = "ComposeWithLLM"
SCRIPT_SUFFIX = ".pyscript"
# Where FL keeps its own piano roll scripts, below its install folder.
STOCK_SCRIPTS_DIR = Path("System") / "Config" / "Piano roll scripts"
TOOLS_MENU_KEY = ord("T")

MENU_TIMEOUT = 1.5  # seconds for a popup menu to open or close
MENU_POLL_INTERVAL = 0.02
ATTEMPTS = 3
RETRY_DELAY = 0.4


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


def _wait_for_menus(count: int) -> bool:
    """Wait until FL has exactly `count` popup menus open."""
    deadline = time.monotonic() + MENU_TIMEOUT
    while True:
        if fl_windows.popup_menu_count() == count:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(MENU_POLL_INTERVAL)


def run_script_from_menu(name: str = SCRIPT_NAME) -> bool:
    """Run a piano roll script through the piano roll's Tools menu.

    The piano roll must be showing. Returns True if the menu was walked to the
    script and Enter pressed there; the caller confirms the script ran from its
    output.
    """
    if platform.system() != "Windows":
        return False
    try:
        with keyboard_lock:
            after = scripts_after(name, _script_folders())
            if after is None:
                return False
            for _ in range(ATTEMPTS):
                outcome = _walk_to_script(after)
                if outcome is not None:
                    return outcome
                time.sleep(RETRY_DELAY)  # the pointer was over the menu: let it move on
            return False
    except Exception:
        logger.exception("Running %s from the piano roll menu failed", name)
        return False


def _walk_to_script(after: int) -> bool | None:
    """One walk: True if Enter was pressed on the script, None if the pointer got in the way."""
    if fl_windows.popup_menu_count() != 0 or not fl_windows.click_piano_roll_menu_arrow():
        return False
    try:
        if not _wait_for_menus(1):
            return False
        if not fl_windows.press_in_popup_menu(TOOLS_MENU_KEY, menus=1) or not _wait_for_menus(2):
            return False
        # End goes to the submenu's last entry wherever the highlight was (the
        # pointer may have put it anywhere); the scripts are listed last.
        for key in [fl_windows.VK_END] + [fl_windows.VK_UP] * after:
            # Still exactly the menu and its Tools submenu: no key opened or closed one.
            if not fl_windows.press_in_popup_menu(key, menus=2):
                return False
        if fl_windows.pointer_over_menu():
            return None
        return fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=2)
    finally:
        fl_windows.close_popup_menus()  # no menu may be left open, whatever happened


def close_menus_left_open() -> None:
    """Close menus still open once a run has gone unanswered.

    Right after Enter a menu counts as closed while it fades; one that FL left
    open without acting is only seen, and closed, after that. Never raises.
    """
    try:
        fl_windows.close_popup_menus()
    except Exception:
        logger.exception("Could not close FL Studio's menus")


def close_script_dialogs() -> int:
    """Close the settings windows of piano roll scripts; returns how many there were.

    One showing up after a walk means the walk reached another script. Closing
    it discards that script's changes.
    """
    count = fl_windows.window_count(fl_windows.SCRIPT_DIALOG_CLASS)
    if count:
        fl_windows.close_windows(fl_windows.SCRIPT_DIALOG_CLASS)
    return count
