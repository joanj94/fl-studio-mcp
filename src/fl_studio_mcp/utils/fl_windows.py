"""Looking inside FL Studio's window on Windows, and pressing things in it without the focus.

FL's panels (piano roll, channel rack, ...) are child windows with a caption
child carrying the panel's title, every open popup menu is a top-level window
of its own, and plugin windows are children of the main window. Clicks and keys
are *posted* to those windows: FL handles them while it stays in the background,
so nothing can land in the program the user is working in. Seen in FL Studio
2026; Windows only.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils import win_focus

CAPTION_CLASS = "TNewCaption"
POPUP_MENU_CLASS = "TQuickPopupMenuWindow"
RENDER_WINDOW_CLASS = "TWAVRenderForm"
# Windows' standard dialog class: FL's file dialogs (the title depends on the language).
SYSTEM_DIALOG_CLASS = "#32770"
PIANO_ROLL_TITLE = "Piano roll"
PLAYLIST_TITLE = "Playlist"
CHANNEL_RACK_TITLE = "Channel rack"
BROWSER_TITLE = "Browser"
BROWSER_TREE_CLASS = "TDataBrowserTree"  # the browser's list of folders and files
# A point on the browser tree's first row (pixels from the tree's top-left corner).
BROWSER_ROW_OFFSET = (60, 10)
CHANNEL_LIST_CLASS = "TVectorPanel"  # the rack's list of channels (and some of its bars)
EDITOR_PANEL_CLASS = "TEventEditForm"  # the piano roll's and the playlist's window
# In the playlist: the box above the track headers, left of the time ruler.
PLAYLIST_CORNER_CLASS = "TWPControl"
MAIN_MENU_CLASS = "TNewMenu"  # the FILE EDIT ADD ... bar
# A point inside "FILE", the bar's first entry (pixels from the bar's top-left corner).
FILE_MENU_OFFSET = (12, 16)
EDIT_CLASS = "Edit"
# The menu arrow sits at the caption's left edge (pixels from its top-left corner).
MENU_ARROW_OFFSET = (11, 11)

_WM_KEYDOWN = 0x0100
_WM_KEYUP = 0x0101
_WM_LBUTTONDOWN = 0x0201
_WM_LBUTTONUP = 0x0202
_MK_LBUTTON = 0x0001
_WM_RBUTTONDOWN = 0x0204
_WM_RBUTTONUP = 0x0205
_MK_RBUTTON = 0x0002
_WM_CLOSE = 0x0010
_WM_SETTEXT = 0x000C
_WM_COMMAND = 0x0111
_IDOK = 1
_IDCANCEL = 2
_SMTO_ABORTIFHUNG = 0x0002
_SEND_TIMEOUT_MS = 2000
_SW_SHOWNOACTIVATE = 4
_HWND_BOTTOM = 1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
VK_END = 0x23
VK_HOME = 0x24
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
PLUGIN_WINDOW_CLASS = "TPluginForm"
# FL's own message boxes ("... is not included in the edition you are testing", questions).
MESSAGE_WINDOW_CLASS = "TMsgForm"
MESSAGE_BUTTON_CLASS = "TQuickFocusBtn"  # its buttons, e.g. Ok and Cancel, left to right
MESSAGE_BUTTON_OFFSET = (20, 12)  # a point inside a button
# The settings window of a piano roll script that has one (the stock scripts do).
SCRIPT_DIALOG_CLASS = "TScriptDialog"
# Seen live: after an entry is chosen FL acts at once, but the menu's window fades
# out for most of a second. A menu still there after this long is a menu left open.
MENU_FADE_SECONDS = 1.5
# The playlist's frame and scroll bar below the last track row that is safe to click.
PLAYLIST_BOTTOM_MARGIN = 30

# Menus an entry was chosen in, by handle, with the time it was chosen.
_chosen_menus: dict[int, float] = {}

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


def top_level_handles(user32: Any, kernel32: Any, class_name: str) -> list[int]:
    """Handles of FL's visible top-level windows of one window class."""
    return [
        hwnd
        for hwnd in _enumerate(user32.EnumWindows)
        if user32.IsWindowVisible(hwnd)
        and _class_name(user32, hwnd) == class_name
        and win_focus._owned_by_fl(user32, kernel32, hwnd)
    ]


def top_level_windows(user32: Any, kernel32: Any, class_name: str) -> list[Rect]:
    """Rectangles of FL's visible top-level windows of one window class."""
    return [_rect(user32, hwnd) for hwnd in top_level_handles(user32, kernel32, class_name)]


def open_menus(user32: Any, kernel32: Any) -> list[int]:
    """Handles of FL's open popup menus (a submenu is a menu of its own), outermost first.

    Menus that are only fading out after a choice are left out.
    """
    handles = top_level_handles(user32, kernel32, POPUP_MENU_CLASS)
    now = time.monotonic()
    for hwnd, chosen_at in list(_chosen_menus.items()):
        if hwnd not in handles or now - chosen_at > MENU_FADE_SECONDS:
            del _chosen_menus[hwnd]
    # EnumWindows lists the topmost window first: the innermost submenu.
    return [hwnd for hwnd in reversed(handles) if hwnd not in _chosen_menus]


def post_key_to_popup_menu(
    user32: Any, kernel32: Any, virtual_key: int, menus: int, chooses: bool = False
) -> bool:
    """Press a key in FL's innermost open popup menu by posting it to the menu's window.

    Seen live: the menu takes it without FL being the active window. Only done
    when FL has exactly `menus` menus open (a submenu counts), so the key can't
    land in a menu other than the expected one. After Enter the menus count as
    closed: FL has acted, though their windows take a moment to fade. The same
    goes for an entry's accelerator letter, which chooses it at once: pass
    `chooses` for those.
    """
    handles = open_menus(user32, kernel32)
    if len(handles) != menus:
        return False
    menu = wintypes.HWND(handles[-1])
    sent = user32.PostMessageW(menu, _WM_KEYDOWN, virtual_key, 0)
    sent = bool(sent and user32.PostMessageW(menu, _WM_KEYUP, virtual_key, 0))
    if sent and (chooses or virtual_key == VK_RETURN):
        _chosen_menus.update(dict.fromkeys(handles, time.monotonic()))
    return sent


def _post_click(user32: Any, hwnd: int, offset: tuple[int, int]) -> bool:
    """Left-click a window at a point inside it, by posting the click to it.

    The click goes to that window wherever it is on screen, even covered.
    """
    target = wintypes.HWND(hwnd)
    position = (offset[1] << 16) | offset[0]
    sent = user32.PostMessageW(target, _WM_LBUTTONDOWN, _MK_LBUTTON, position)
    return bool(sent and user32.PostMessageW(target, _WM_LBUTTONUP, 0, position))


def _visible_children(user32: Any, parent: int, class_name: str) -> list[int]:
    return [
        hwnd for hwnd in _enumerate(user32.EnumChildWindows, parent)
        if user32.IsWindowVisible(hwnd) and _class_name(user32, hwnd) == class_name
    ]


def click_panel_menu_arrow(user32: Any, main: int, title: str) -> bool:
    """Click a panel's menu arrow by posting the click to the panel's caption."""
    found = panel_caption(user32, main, title)
    return found is not None and _post_click(user32, found[0], MENU_ARROW_OFFSET)


def click_main_menu() -> bool:
    """Open FL's File menu (the first entry of its menu bar). False if FL isn't running."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return False
    bars = _visible_children(user32, main, MAIN_MENU_CLASS)
    return bool(bars) and _post_click(user32, bars[0], FILE_MENU_OFFSET)


def confirm_dialog_with_file_name(file_name: str) -> bool:
    """Put a file name into FL's open file dialog and press its default button.

    The text is set in the dialog's own edit box and the button pressed by
    message, so the dialog needn't have the focus. False unless exactly one
    dialog with one file name box is open.
    """
    user32, kernel32 = win_focus._libraries()
    dialogs = top_level_handles(user32, kernel32, SYSTEM_DIALOG_CLASS)
    if len(dialogs) != 1:
        return False
    boxes = _visible_children(user32, dialogs[0], EDIT_CLASS)
    if len(boxes) != 1:
        return False
    result = ctypes.c_size_t(0)
    if not user32.SendMessageTimeoutW(
        boxes[0], _WM_SETTEXT, 0, file_name, _SMTO_ABORTIFHUNG, _SEND_TIMEOUT_MS,
        ctypes.byref(result),
    ):
        return False
    return bool(user32.PostMessageW(wintypes.HWND(dialogs[0]), _WM_COMMAND, _IDOK, 0))


def cancel_dialogs() -> None:
    """Press Cancel in FL's open system dialogs."""
    user32, kernel32 = win_focus._libraries()
    for dialog in top_level_handles(user32, kernel32, SYSTEM_DIALOG_CLASS):
        user32.PostMessageW(wintypes.HWND(dialog), _WM_COMMAND, _IDCANCEL, 0)


def press_in_window(class_name: str, virtual_key: int) -> bool:
    """Press a key in FL's one top-level window of a class. False unless exactly one is open."""
    user32, kernel32 = win_focus._libraries()
    windows = top_level_handles(user32, kernel32, class_name)
    if len(windows) != 1:
        return False
    window = wintypes.HWND(windows[0])
    sent = user32.PostMessageW(window, _WM_KEYDOWN, virtual_key, 0)
    return bool(sent and user32.PostMessageW(window, _WM_KEYUP, virtual_key, 0))


def open_message() -> str | None:
    """Title of the message box FL Studio is showing, or None.

    Seen live: while one is open FL answers reads but silently ignores changes
    (renames, routing), so callers check before changing anything. Its text
    can't be read from outside, only its title. Never raises: off Windows, or
    if the windows can't be listed, there is simply no message to report.
    """
    try:
        user32, kernel32 = win_focus._libraries()
        messages = top_level_handles(user32, kernel32, MESSAGE_WINDOW_CLASS)
        return win_focus._window_title(user32, messages[0]) if messages else None
    except Exception:
        return None


def close_messages() -> int:
    """Close FL's message boxes as their X button would; returns how many.

    Seen live: a posted Enter does nothing there, a posted WM_CLOSE closes it.
    For a question that is "cancel".
    """
    user32, kernel32 = win_focus._libraries()
    messages = top_level_handles(user32, kernel32, MESSAGE_WINDOW_CLASS)
    for message in messages:
        user32.PostMessageW(wintypes.HWND(message), _WM_CLOSE, 0, 0)
    return len(messages)


def click_message_button(position: int) -> bool:
    """Click a button of FL's message box, counted from the left (-1 is the last one).

    Seen live: the box ignores a posted Enter, but its buttons take a posted
    click. False unless exactly one box is open and it has that button.
    """
    user32, kernel32 = win_focus._libraries()
    messages = top_level_handles(user32, kernel32, MESSAGE_WINDOW_CLASS)
    if len(messages) != 1:
        return False
    buttons = sorted(
        _visible_children(user32, messages[0], MESSAGE_BUTTON_CLASS),
        key=lambda hwnd: _rect(user32, hwnd)[0],
    )
    if not -len(buttons) <= position < len(buttons):
        return False
    return _post_click(user32, buttons[position], MESSAGE_BUTTON_OFFSET)


def close_windows(class_name: str) -> None:
    """Ask FL's top-level windows of a class to close."""
    user32, kernel32 = win_focus._libraries()
    for window in top_level_handles(user32, kernel32, class_name):
        user32.PostMessageW(wintypes.HWND(window), _WM_CLOSE, 0, 0)


def pointer_over_menu() -> bool:
    """True if the mouse pointer is over one of FL's open popup menus.

    A menu highlights the entry under the pointer, also when the menu opens
    under a pointer that isn't moving. A walk by arrow keys starts from the
    highlighted entry, so it must not be trusted while this is True.
    """
    user32, kernel32 = win_focus._libraries()
    point = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(point)):
        return True  # unknown: assume the worst
    return any(
        left <= point.x < right and top <= point.y < bottom
        for left, top, right, bottom in (
            _rect(user32, menu) for menu in open_menus(user32, kernel32)
        )
    )


def click_piano_roll_menu_arrow() -> bool:
    """Open the piano roll's menu. False if no piano roll is showing."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and click_panel_menu_arrow(user32, main, PIANO_ROLL_TITLE)


@dataclass(frozen=True)
class PlaylistPanel:
    """The playlist's window and where its parts are, in the window's own coordinates."""

    hwnd: int
    header_left: int  # the track headers start here...
    grid_left: int  # ...and end where the clip area starts
    grid_top: int
    grid_bottom: int


def playlist_panel() -> PlaylistPanel | None:
    """The playlist as docked in FL's main window; None if it isn't showing there.

    Seen live: the box above the track headers is a child window of its own. It
    ends where the clip area begins, to its right and below it.
    """
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return None
    for hwnd in _visible_children(user32, main, EDITOR_PANEL_CLASS):
        if not win_focus._window_title(user32, hwnd).startswith(PLAYLIST_TITLE):
            continue
        corners = _visible_children(user32, hwnd, PLAYLIST_CORNER_CLASS)
        if len(corners) != 1:
            return None
        left, top, _right, bottom = _rect(user32, hwnd)
        corner = _rect(user32, corners[0])
        return PlaylistPanel(
            hwnd=hwnd,
            header_left=corner[0] - left,
            grid_left=corner[2] - left,
            grid_top=corner[3] - top,
            grid_bottom=bottom - top - PLAYLIST_BOTTOM_MARGIN,
        )
    return None


def click_in_window(hwnd: int, x: int, y: int) -> bool:
    """Left-click at a point of one of FL's windows (its own coordinates), by posted message."""
    return _post_click(win_focus._libraries()[0], hwnd, (x, y))


def press_key_in_window(hwnd: int, virtual_key: int) -> bool:
    """Press a key in one of FL's windows, by posted message."""
    user32, _ = win_focus._libraries()
    sent = user32.PostMessageW(wintypes.HWND(hwnd), _WM_KEYDOWN, virtual_key, 0)
    return bool(sent and user32.PostMessageW(wintypes.HWND(hwnd), _WM_KEYUP, virtual_key, 0))


def click_playlist_menu_arrow() -> bool:
    """Open the playlist's menu. False if no playlist is showing."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and click_panel_menu_arrow(user32, main, PLAYLIST_TITLE)


def click_channel_rack_menu_arrow() -> bool:
    """Open the channel rack's menu. False if no channel rack is showing."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and click_panel_menu_arrow(user32, main, CHANNEL_RACK_TITLE)


def click_browser_menu_arrow() -> bool:
    """Open the browser's menu. False if no browser is showing."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and click_panel_menu_arrow(user32, main, BROWSER_TITLE)


def click_browser_tree() -> bool:
    """Click the browser tree's first row, which gives the browser a cursor again."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return False
    trees = _visible_children(user32, main, BROWSER_TREE_CLASS)
    return bool(trees) and _post_click(user32, trees[0], BROWSER_ROW_OFFSET)


def channel_list_panel() -> tuple[int, int] | None:
    """Handle and height of the channel rack's list of channels; None if no rack is showing.

    Seen live: the rack's form has several `TVectorPanel` children; the list with
    the channel buttons and steps is the largest one showing.
    """
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    found = None if main is None else panel_caption(user32, main, CHANNEL_RACK_TITLE)
    if found is None:
        return None
    rack = user32.GetParent(wintypes.HWND(found[0]))
    panels = [
        (bottom - top, right - left, hwnd)
        for hwnd in _visible_children(user32, rack, CHANNEL_LIST_CLASS)
        for left, top, right, bottom in [_rect(user32, hwnd)]
    ]
    if not panels:
        return None
    height, _width, hwnd = max(panels, key=lambda panel: panel[0] * panel[1])
    return hwnd, height


def right_click_in_window(hwnd: int, x: int, y: int) -> bool:
    """Right-click at a point of one of FL's windows (its own coordinates), by posted message."""
    user32, _ = win_focus._libraries()
    target = wintypes.HWND(hwnd)
    position = (y << 16) | x
    sent = user32.PostMessageW(target, _WM_RBUTTONDOWN, _MK_RBUTTON, position)
    return bool(sent and user32.PostMessageW(target, _WM_RBUTTONUP, 0, position))


def popup_menu_count() -> int:
    """How many popup menus FL Studio has open right now."""
    return len(open_menus(*win_focus._libraries()))


def popup_menu_rects() -> list[Rect]:
    """Screen rectangles of FL's open popup menus, outermost first."""
    user32, kernel32 = win_focus._libraries()
    return [_rect(user32, menu) for menu in open_menus(user32, kernel32)]


def restore_behind(user32: Any, main: int) -> bool:
    """Un-minimize FL's window without giving it the focus, behind the other windows.

    Seen live: while FL is minimized the browser's item menu doesn't open (the
    piano roll's menu and everything in the API still work).
    """
    if not user32.IsIconic(wintypes.HWND(main)):
        return False
    user32.ShowWindow(wintypes.HWND(main), _SW_SHOWNOACTIVATE)
    flags = _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE
    user32.SetWindowPos(wintypes.HWND(main), wintypes.HWND(_HWND_BOTTOM), 0, 0, 0, 0, flags)
    return True


def restore_if_minimized() -> bool:
    """Bring FL's window back if it is minimized, without the focus. True if it was."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return main is not None and restore_behind(user32, main)


def close_plugin_windows(user32: Any, main: int) -> int:
    """Close every open plugin window (instruments, samplers, effects); returns how many.

    FL's API can close none of an effect's windows and only toggles a sampler's.
    Closing the window leaves the plugin in place.
    """
    closed = 0
    for hwnd in _enumerate(user32.EnumChildWindows, main):
        if user32.IsWindowVisible(hwnd) and _class_name(user32, hwnd) == PLUGIN_WINDOW_CLASS:
            closed += bool(user32.PostMessageW(wintypes.HWND(hwnd), _WM_CLOSE, 0, 0))
    return closed


def close_all_plugin_windows() -> int:
    """Close FL's open plugin windows; returns how many were asked to close."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    return 0 if main is None else close_plugin_windows(user32, main)


def close_popup_menus() -> None:
    """Back out of whatever popup menus FL has open, innermost first."""
    user32, kernel32 = win_focus._libraries()
    for menu in reversed(open_menus(user32, kernel32)):
        user32.PostMessageW(wintypes.HWND(menu), _WM_KEYDOWN, VK_ESCAPE, 0)
        user32.PostMessageW(wintypes.HWND(menu), _WM_KEYUP, VK_ESCAPE, 0)


def press_in_popup_menu(virtual_key: int, menus: int = 1, chooses: bool = False) -> bool:
    """Press a key in FL's open popup menu. False unless exactly `menus` menus are open.

    `chooses`: the key is an entry's accelerator letter, which closes the menus.
    """
    return post_key_to_popup_menu(*win_focus._libraries(), virtual_key, menus, chooses)


def press_enter_in_popup_menu() -> bool:
    """Choose the highlighted entry of FL's open popup menu. False unless exactly one is open."""
    return press_in_popup_menu(VK_RETURN)


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
