"""Finding items in FL Studio's browser through the controller.

FL's API only moves the browser's cursor up and down and tells what it is on;
it doesn't say how deep an item is. So a folder is read like this: every folder
the cursor reaches is closed, which keeps the walk on one level, and a folder's
items end where the next item of the level above begins.

All of it goes through FL's API (no keys, no mouse), so FL needn't be in front.
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.connection import call

FOLDER_TYPE = -100  # folders and collections have this file type or lower
STEPS_PER_COMMAND = 100  # about 15 ms a file: kept well inside the response timeout
MAX_ITEMS = 20000  # a walk longer than this is given up

# An item's menu starts the same way for every kind of file (seen live, FL 2026):
# entry 1 sends it to the selected channel, entry 2 opens it in a new channel.
# An effect's menu has only the first of the two. A sample's menu goes on with "Send to
# playlist as an audio clip": FL puts the clip at the song position, on the first free
# playlist track, unstretched (seen live, FL 2026).
SEND_TO_SELECTED_CHANNEL = 1
OPEN_IN_NEW_CHANNEL = 2
SEND_TO_PLAYLIST_AS_AUDIO_CLIP = 3
MENU_TIMEOUT = 2.0  # seconds for the menu to open or close
MENU_POLL_INTERVAL = 0.05
RESTORE_DELAY = 0.5  # seconds FL gets to draw its window after being un-minimized
# How many items past the end of a folder are compared with what should follow it.
TAIL_CHECK = 2
CHANGED = "The browser changed while it was being read; try again."
# "Reread structure": third from the bottom of the browser caption's menu.
REREAD_KEYS = (fl_windows.VK_END, fl_windows.VK_UP, fl_windows.VK_UP)
REREAD_SECONDS = 2.0  # FL reads its folders again (it says "Browser refreshed")
REREAD_CLICK_SETTLE = 0.5
# What can be loaded: FL's menu for other files (projects, scores, ...) starts differently.
# Seen live: a Morphine preset (.mrp) has the same menu as an .fst and loads the same way.
LOADABLE_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff", ".wv", ".fst", ".mrp")


class BrowserError(Exception):
    """The browser could not be read or the item isn't there."""


@dataclass(frozen=True)
class BrowserItem:
    name: str
    file_type: int

    @property
    def is_folder(self) -> bool:
        return self.file_type <= FOLDER_TYPE

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": "folder" if self.is_folder else "file"}


def _command(action: str, params: dict | None = None) -> dict:
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise BrowserError(str(e)) from e
    if "error" in result:
        raise BrowserError(result["error"])
    return result


def _item(node: dict) -> BrowserItem:
    return BrowserItem(str(node.get("name", "")), int(node.get("file_type", 0)))


def focused() -> BrowserItem:
    return _item(_command("browser.getFocused"))


def _walk(direction: str, until: BrowserItem | None = None) -> tuple[list[BrowserItem], bool]:
    """Walk to the end of the list, or to `until`; returns the items reached and if found."""
    reached: list[BrowserItem] = []
    pending: list[BrowserItem] = []  # repeats of the last item: real only if the walk goes on
    while len(reached) <= MAX_ITEMS:
        params: dict = {"direction": direction, "steps": STEPS_PER_COMMAND, "collapse": True}
        if until is not None:
            params.update(until=until.name, until_type=until.file_type)
        result = _command("browser.navigate", params)
        items = [_item(node) for node in result.get("items", [])]
        if items:
            reached.extend(pending)
        reached.extend(items)
        pending = [_item(node) for node in result.get("pending", [])]
        if result.get("found"):
            return reached, True
        if result.get("stuck") or not items:  # the cursor didn't move: the end of the list
            return reached, False
    raise BrowserError(f"The browser has more than {MAX_ITEMS} items here; giving up.")


def _peek(count: int) -> list[BrowserItem]:
    """Step on `count` items (fewer at the end of the list) and return them."""
    if count == 0:
        return []
    params = {"direction": "next", "steps": count, "collapse": True}
    return [_item(node) for node in _command("browser.navigate", params).get("items", [])]


def _step(direction: str, steps: int) -> None:
    while steps > 0:
        count = min(steps, STEPS_PER_COMMAND)
        _command("browser.navigate", {"direction": direction, "steps": count})
        steps -= count


def _root() -> list[BrowserItem]:
    """Close everything, list the top level and leave the cursor on its first item."""
    _command("ui.focusWindow", {"window": "browser"})
    # The tree of files is the first tab; the others (plugins, sounds from the cloud)
    # have their own lists, where the cursor reports nothing.
    _command("browser.selectTab", {"direction": "first"})
    _walk("previous")
    _command("browser.toggleNode", {"value": 0})  # the first item itself, if it is a folder
    first = focused()
    below, _ = _walk("next")
    _step("previous", len(below))
    return [first, *below]


def _find(level: list[BrowserItem], name: str, where: str) -> int:
    wanted = name.strip().lower()
    matches = [i for i, item in enumerate(level) if item.name.lower() == wanted]
    if not matches:
        matches = [i for i, item in enumerate(level)
                   if not item.is_folder and item.name.rsplit(".", 1)[0].lower() == wanted]
    if len(matches) != 1:
        problem = "is not" if not matches else "is more than once"
        raise BrowserError(f"{name!r} {problem} in {where}. Use fl_browser_list to see what is.")
    return matches[0]


def _read_open_folder(tail: list[BrowserItem]) -> tuple[list[BrowserItem], int]:
    """List the open folder the cursor is on; returns its items and how far the cursor went.

    `tail` is what follows the folder once it is closed: the rest of its level,
    then the rest of the levels above. The folder's items end where the tail
    begins. An item of the folder may look like the tail's first item (a "Loops"
    folder inside, and another one next door), so the items after it must match
    the tail as well.
    """
    reached: list[BrowserItem] = []
    while len(reached) <= MAX_ITEMS:
        more, found = _walk("next", tail[0] if tail else None)
        reached.extend(more)
        if not tail:
            return reached, len(reached)
        if not found:
            raise BrowserError(CHANGED)
        expected = tail[1:1 + TAIL_CHECK]
        ahead = _peek(len(expected))
        if ahead == expected:
            return reached[:-1], len(reached) + len(ahead)
        reached.extend(ahead)  # that look-alike was one of the folder's own items
    raise BrowserError(f"The browser has more than {MAX_ITEMS} items here; giving up.")


def _open_folder(
    level: list[BrowserItem], index: int, offset: int, tail: list[BrowserItem]
) -> tuple[list[BrowserItem], list[BrowserItem]]:
    """Move to level[index], open it and list it; the cursor ends on that folder.

    `offset` is how far level[0] is from the cursor. Returns the folder's items
    and what follows the folder (the tail for a folder inside it).
    """
    folder = level[index]
    if not folder.is_folder:
        raise BrowserError(f"{folder.name!r} is a file, not a folder.")
    _step("next", index + offset)
    if focused() != folder:
        raise BrowserError(CHANGED)
    tail = [*level[index + 1:], *tail]
    _command("browser.toggleNode", {"value": 1})
    children, moved = _read_open_folder(tail)
    _step("previous", moved)
    if focused() != folder:
        raise BrowserError(CHANGED)
    return children, tail


def split_path(path: str) -> list[str]:
    return [part.strip() for part in path.replace("\\", "/").split("/") if part.strip()]


def list_folder(path: str) -> list[BrowserItem]:
    """Items of a browser folder ("" is the top level), e.g. "Packs/Drums/Kicks".

    Leaves the cursor on that folder, with the folder open.
    """
    level = _root()
    tail: list[BrowserItem] = []
    offset = 0
    where = "the browser's top level"
    for name in split_path(path):
        level, tail = _open_folder(level, _find(level, name, where), offset, tail)
        offset = 1
        where = repr(name)
    return level


def go_to_file(path: str) -> BrowserItem:
    """Put the browser's cursor on a file, e.g. "Packs/Drums/Kicks/Kick 01.wav"."""
    parts = split_path(path)
    if len(parts) < 2:
        raise BrowserError("Give the file with its folders, like 'Packs/Drums/Kicks/Kick 01'.")
    # Seen live: while FL is minimized the file's menu doesn't open, and the browser
    # doesn't take FL's focus either, so FL is brought back before the browser is touched.
    if platform.system() == "Windows" and fl_windows.restore_if_minimized():
        time.sleep(RESTORE_DELAY)
    level = list_folder("/".join(parts[:-1]))
    target = level[_find(level, parts[-1], repr(parts[-2]))]
    if target.is_folder:
        raise BrowserError(f"{target.name!r} is a folder, not a file.")
    if not target.name.lower().endswith(LOADABLE_SUFFIXES):
        raise BrowserError(
            f"{target.name!r} is not a sample or a preset (.fst, .mrp), so it can't be loaded."
        )
    _step("next", level.index(target) + 1)
    if focused() != target:
        raise BrowserError(CHANGED)
    return target


def reread_structure() -> None:
    """Make FL's browser read its folders from disk again ("Reread structure").

    Seen live: the browser caches folder listings, so files written while FL runs
    don't show until then. The entry is third from the bottom of the browser's
    caption menu. Afterwards the browser has no cursor (every walk fails) until a
    row of its tree gets a click, so one is posted to its first row. Windows only.
    """
    if platform.system() != "Windows":
        raise BrowserError("The browser can only be reread on Windows.")
    if fl_windows.popup_menu_count() != 0:
        raise BrowserError("FL Studio has a menu open. Close it and try again.")
    if not fl_windows.click_browser_menu_arrow():
        raise BrowserError("FL Studio's browser isn't showing.")
    try:
        if not _wait_for_menus(1):
            raise BrowserError("The browser's menu did not open.")
        for key in REREAD_KEYS:
            if not fl_windows.press_in_popup_menu(key):
                raise BrowserError("The browser's menu closed unexpectedly.")
        if fl_windows.pointer_over_menu():
            raise BrowserError(
                "The mouse pointer is over FL Studio's menu, where it moves the highlight, "
                "so nothing was chosen. Try again with the pointer elsewhere."
            )
        if not fl_windows.press_enter_in_popup_menu():
            raise BrowserError("The browser's menu did not take the choice.")
    finally:
        if fl_windows.popup_menu_count():
            fl_windows.close_popup_menus()
    time.sleep(REREAD_SECONDS)
    if not fl_windows.click_browser_tree():
        raise BrowserError("The browser's tree was not found after rereading it.")
    time.sleep(REREAD_CLICK_SETTLE)


def _wait_for_menus(count: int) -> bool:
    deadline = time.monotonic() + MENU_TIMEOUT
    while True:
        if fl_windows.popup_menu_count() == count:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(MENU_POLL_INTERVAL)


def choose_from_menu(entry: int) -> None:
    """Open the focused item's menu and choose its entry number `entry` (1 is the first).

    The menu is opened through FL's API and the keys are posted to the menu's
    own window, so FL needn't be the active window. Home goes to the first
    entry wherever the highlight was (a mouse pointer over the menu moves it),
    and Enter is only pressed while the pointer is off the menu: further down
    the menu are entries like "Delete file...". The entries can't be read, so
    the caller checks what happened. Windows only.
    """
    if platform.system() != "Windows":
        raise BrowserError("Loading from the browser is only supported on Windows.")
    if fl_windows.popup_menu_count() != 0:
        raise BrowserError("FL Studio has a menu open. Close it and try again.")
    _command("browser.openFocused")
    try:
        if not _wait_for_menus(1):
            raise BrowserError("FL Studio did not open the item's menu.")
        for key in [fl_windows.VK_HOME] + [fl_windows.VK_DOWN] * (entry - 1):
            if not fl_windows.press_in_popup_menu(key):
                raise BrowserError("The item's menu closed before an entry was chosen.")
        if fl_windows.pointer_over_menu():
            raise BrowserError(
                "The mouse pointer is over FL Studio's menu, where it moves the highlight, "
                "so nothing was chosen. Try again with the pointer elsewhere."
            )
        if not fl_windows.press_enter_in_popup_menu():
            raise BrowserError("The item's menu did not take the choice.")
    finally:
        if fl_windows.popup_menu_count():
            fl_windows.close_popup_menus()
