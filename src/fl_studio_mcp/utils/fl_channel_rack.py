"""What only the channel rack's menus can do, without taking the focus.

FL's API can't delete a channel. The rack's menu (the arrow in its caption) has
"Delete selected...", which asks for confirmation in a message box. The click,
the keys and the click on the box's Ok button are all posted to FL's own
windows (`fl_windows`). Windows only.

"Cut itself" is in the menu of a channel's own button (a posted right-click),
and its state is read from a picture of that menu (`set_cut_itself`).

Menu entries can't be read from outside FL, so the entry is reached by
position and the walk is checked by what it does: the rack must end up exactly
as many channels shorter as were selected.
"""

from __future__ import annotations

import platform
import time
from collections import Counter

from fl_studio_mcp.utils import fl_capture, fl_windows
from fl_studio_mcp.utils.connection import call

MENU_TIMEOUT = 1.5  # seconds for the rack's menu to open
CONFIRM_TIMEOUT = 1.5  # seconds for FL's confirmation box to show up
DELETE_TIMEOUT = 2.0  # seconds for the rack to get shorter
POLL_INTERVAL = 0.02
ATTEMPTS = 3
RETRY_DELAY = 0.4
# Seen live (FL 2026 trial): from the menu's first entry, "Add one", the arrow
# keys go to Loop starter, Purge unused audio clips, Select unused channels and
# then Delete selected: "Clone selected" in between is disabled in the trial
# and skipped. Where it is enabled the walk ends on it, which the check on the
# rack's length catches (the clone is undone).
DOWNS_TO_DELETE = 4
OK_BUTTON = 0  # the confirmation's buttons are Ok and Cancel, left to right
CONFIRM_TITLE = "Confirm"  # the title of the box "The selected channel is about to be deleted"
# Seen live (FL 2026, 100 % scaling), in the rack's list of channels: a point inside
# the first channel's name button, and the distance between two rows.
BUTTON_X = 144
FIRST_BUTTON_Y = 32
ROW_HEIGHT = 30
BUTTON_ATTEMPTS = 4
# The menu of a Sampler channel's button: Piano roll, Graph editor, Rename, Change
# color, Random color, Change icon, Load sample, Cut itself (all enabled).
DOWNS_TO_CUT_ITSELF = 7
KEY_SETTLE = 0.15
# In an entry's row, as shares of its height from its left edge: where the check
# mark ends and where the text begins (seen live: a 19 px row, mark to 9, text from 14).
CHECK_ENDS_AT = 0.6
TEXT_STARTS_AT = 0.65
CHECKED_SHARE = 0.6  # a mark at least this much as dark as the text is on


class ChannelRackError(Exception):
    """The channel rack could not be driven; the message says why."""


def _fl(action: str, params: dict | None = None) -> dict:
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise ChannelRackError(str(e)) from e
    if "error" in result:
        raise ChannelRackError(result["error"])
    return result


def _wait_until(done, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if done():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def _walk_to_delete() -> bool | None:
    """One menu walk: True if Delete selected was chosen, None if the pointer got in the way."""
    if fl_windows.popup_menu_count() != 0 or not fl_windows.click_channel_rack_menu_arrow():
        return False
    try:
        if not _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_TIMEOUT):
            return False
        for key in [fl_windows.VK_HOME] + [fl_windows.VK_DOWN] * DOWNS_TO_DELETE:
            if not fl_windows.press_in_popup_menu(key, menus=1):
                return False
        # The pointer moves a menu's highlight: no choice is made under it.
        if fl_windows.pointer_over_menu():
            return None
        return fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=1)
    finally:
        fl_windows.close_popup_menus()  # no menu may be left open, whatever happened


def _choose_delete() -> None:
    for _ in range(ATTEMPTS):
        outcome = _walk_to_delete()
        if outcome:
            return
        if outcome is False:
            raise ChannelRackError(
                "The channel rack's menu did not open as expected (is the channel rack showing, "
                "or another menu or a dialog open in FL Studio?)"
            )
        time.sleep(RETRY_DELAY)  # the pointer was over the menu: let it move on
    raise ChannelRackError("The mouse pointer is over FL Studio's menu, so nothing was chosen")


def _count() -> int:
    return len(_fl("channels.getAll")["channels"])


def _check_ready() -> None:
    if platform.system() != "Windows":
        raise ChannelRackError("The channel rack's menus are only supported on Windows")
    message = fl_windows.open_message()
    if message is not None:
        raise ChannelRackError(
            f"FL Studio is showing a message box ({message!r}); fl_close_message closes it"
        )


def delete_channels(indexes: list[int]) -> int:
    """Delete the channels at `indexes`; returns how many channels the rack has left.

    Selects exactly those channels, chooses "Delete selected..." and confirms.
    Raises ChannelRackError unless the rack ends up that many channels shorter.
    """
    _check_ready()
    before = _count()
    _fl("channels.selectOne", {"index": indexes[0]})
    for index in indexes[1:]:
        _fl("channels.select", {"index": index, "select": True})
    selected = [c["index"] for c in _fl("channels.getAll")["channels"] if c["is_selected"]]
    if sorted(selected) != sorted(indexes):
        raise ChannelRackError(
            f"FL Studio selected channels {selected} instead of {sorted(indexes)}; none deleted"
        )

    _choose_delete()
    # The box doesn't come when its "Remember my choice" was ticked before.
    if _wait_until(lambda: fl_windows.open_message() is not None, CONFIRM_TIMEOUT):
        title = fl_windows.open_message()
        # Ok is only pressed in the box this entry brings: another entry's question is cancelled.
        if title != CONFIRM_TITLE or not fl_windows.click_message_button(OK_BUTTON):
            fl_windows.close_messages()
            raise ChannelRackError(
                f"FL Studio showed {title!r} instead of its delete confirmation; it was closed "
                "and nothing was deleted"
            )
    wanted = before - len(indexes)
    # Seen live: FL takes the channels out one at a time, so a rack that is only
    # shorter isn't done yet. A longer one is a clone, which comes all at once.
    _wait_until(lambda: fl_windows.open_message() is None and (
        (count := _count()) == wanted or count > before), DELETE_TIMEOUT)
    after = _count()
    if fl_windows.open_message() is not None:  # never leave FL blocked by a box
        fl_windows.close_messages()
    if after > before:  # the walk ended on another entry (Clone selected)
        _fl("project.undo", {"steps": 1})
    if after != wanted:
        raise ChannelRackError(
            f"The rack has {after} channels where {wanted} were expected after deleting "
            f"{len(indexes)} of {before}: the menu entry was not where it was expected"
        )
    return after


# --- "Cut itself", in the menu of a channel's button ---------------------------------


def _selected() -> list[int]:
    return [c["index"] for c in _fl("channels.getAll")["channels"] if c["is_selected"]]


def _select(indexes: list[int]) -> None:
    _fl("channels.selectOne", {"index": indexes[0]})
    for index in indexes[1:]:
        _fl("channels.select", {"index": index, "select": True})


def _close_menu() -> None:
    fl_windows.close_popup_menus()
    _wait_until(lambda: fl_windows.popup_menu_count() == 0, MENU_TIMEOUT)


def _open_button_menu(index: int, count: int) -> None:
    """Right-click channel `index`'s button: leaves its menu open.

    The right-click selects the channel it lands on, which is how the row is
    checked (another channel is selected first) and, after a miss, found.
    """
    panel = fl_windows.channel_list_panel()
    if panel is None:
        raise ChannelRackError("The channel rack isn't showing in FL Studio")
    hwnd, height = panel
    if count > 1:
        _fl("channels.selectOne", {"index": index - 1 if index else 1})
    y = min(FIRST_BUTTON_Y + ROW_HEIGHT * index, height - ROW_HEIGHT // 2)
    for _ in range(BUTTON_ATTEMPTS):
        if (
            fl_windows.popup_menu_count() != 0
            or not fl_windows.right_click_in_window(hwnd, BUTTON_X, y)
            or not _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_TIMEOUT)
        ):
            _close_menu()
            raise ChannelRackError(
                "The channel's menu did not open (is another menu or a dialog open in FL Studio?)"
            )
        selected = _selected()
        if selected == [index]:
            return
        _close_menu()
        if len(selected) != 1:
            break
        y += (index - selected[0]) * ROW_HEIGHT
        if not 0 < y < height:
            raise ChannelRackError(
                f"Channel {index} isn't showing in the channel rack: scroll the rack to it"
            )
    raise ChannelRackError(f"The button of channel {index} was not found in the channel rack")


def _menu_picture() -> tuple[int, bytes]:
    time.sleep(KEY_SETTLE)  # the key was posted: let the menu draw its new highlight
    try:
        picture = fl_capture.menu_pixels()
    except fl_capture.CaptureError as e:
        raise ChannelRackError(str(e)) from e
    if picture is None:
        raise ChannelRackError("FL Studio's menu closed unexpectedly")
    return picture[0], picture[2]


def _luminance(pixels: bytes, offset: int) -> int:
    return (pixels[offset] + pixels[offset + 1] + pixels[offset + 2]) // 3


def entry_is_checked(width: int, before: bytes, after: bytes) -> bool:
    """Whether the menu entry highlighted in `after` but not in `before` has its check mark on.

    Both are pictures of the same menu (blue, green, red, unused; top row first).
    Seen live: FL draws every toggle with a check mark left of its text, faint
    while the toggle is off and as dark as the text while it is on. So the mark
    is compared with the text of its own entry, as drawn without the highlight.
    """
    row = width * 4

    def differs(start: int, length: int) -> bool:
        return before[start:start + length] != after[start:start + length]

    changed = [y for y in range(len(before) // row) if differs(y * row, row)]
    if not changed:
        raise ChannelRackError("The menu's highlight did not move")
    bottom = top = changed[-1]
    while top - 1 in changed:
        top -= 1
    rows = range(top, bottom + 1)
    left = min(x for y in rows for x in range(width) if differs(y * row + x * 4, 4))
    entry_height = len(rows)
    luminances = {
        x: [_luminance(before, y * row + x * 4) for y in rows]
        for x in range(left, min(width, left + 8 * entry_height))
    }
    background = Counter(v for column in luminances.values() for v in column).most_common(1)[0][0]

    def contrast(start: int, end: int) -> int:
        return max(
            (abs(v - background) for x in range(left + start, left + end)
             for v in luminances.get(x, ())),
            default=0,
        )

    text = contrast(int(entry_height * TEXT_STARTS_AT), 8 * entry_height)
    if not text:
        raise ChannelRackError("The menu entry could not be read")
    return contrast(0, int(entry_height * CHECK_ENDS_AT)) >= CHECKED_SHARE * text


def _cut_itself_state(index: int, count: int, downs: int) -> bool | None:
    """Walk to "Cut itself" in the channel's menu and read its check mark.

    The menu is left open on that entry. None if the mouse pointer got in the
    way (it moves the highlight): the menu is then closed.
    """
    _open_button_menu(index, count)
    pictures = []
    for keys in ([fl_windows.VK_HOME], [fl_windows.VK_DOWN] * downs):
        for key in keys:
            if not fl_windows.press_in_popup_menu(key, menus=1):
                _close_menu()
                raise ChannelRackError("The channel's menu closed unexpectedly")
        pictures.append(_menu_picture())
        if fl_windows.pointer_over_menu():
            _close_menu()
            return None
    (width, before), (_, after) = pictures
    return entry_is_checked(width, before, after)


def _walk_to_cut_itself(index: int, count: int, downs: int) -> bool:
    for _ in range(ATTEMPTS):
        state = _cut_itself_state(index, count, downs)
        if state is not None:
            return state
        time.sleep(RETRY_DELAY)  # the pointer was over the menu: let it move on
    raise ChannelRackError("The mouse pointer is over FL Studio's menu, so nothing was chosen")


def set_cut_itself(index: int, enabled: bool) -> bool:
    """Switch a channel's "Cut itself" on or off; returns whether it had to be changed.

    With it on, a new note of the channel cuts the ones still sounding: a long
    sample played faster than it lasts no longer piles up on itself. The entry is
    in the menu of the channel's button, its state is read from its check mark,
    and after a change the menu is opened again to read it back. The channels
    selected before are selected again afterwards.
    """
    _check_ready()
    rack = _fl("channels.getAll")["channels"]
    if not 0 <= index < len(rack):
        raise ChannelRackError(f"No channel at index {index}")
    # Seen live: a Sampler's menu has "Load sample..." above "Cut itself", a plugin's doesn't.
    downs = DOWNS_TO_CUT_ITSELF - (0 if rack[index]["plugin"] is None else 1)
    selected = [c["index"] for c in rack if c["is_selected"]]
    try:
        if _walk_to_cut_itself(index, len(rack), downs) == enabled:
            return False
        if not fl_windows.press_in_popup_menu(fl_windows.VK_RETURN, menus=1):
            raise ChannelRackError("The channel's menu closed unexpectedly")
        _wait_until(lambda: fl_windows.popup_menu_count() == 0, MENU_TIMEOUT)
        if _walk_to_cut_itself(index, len(rack), downs) != enabled:
            raise ChannelRackError(
                "\"Cut itself\" did not change: the menu entry was not where it was expected"
            )
        return True
    finally:
        _close_menu()
        if selected:
            _select(selected)
