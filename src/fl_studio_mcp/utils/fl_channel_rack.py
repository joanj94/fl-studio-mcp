"""Deleting channels through the channel rack's own menu, without taking the focus.

FL's API can't delete a channel. The rack's menu (the arrow in its caption) has
"Delete selected...", which asks for confirmation in a message box. The click,
the keys and the click on the box's Ok button are all posted to FL's own
windows (`fl_windows`). Windows only.

Menu entries can't be read from outside FL, so the entry is reached by
position and the walk is checked by what it does: the rack must end up exactly
as many channels shorter as were selected.
"""

from __future__ import annotations

import platform
import time

from fl_studio_mcp.utils import fl_windows
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


def delete_channels(indexes: list[int]) -> int:
    """Delete the channels at `indexes`; returns how many channels the rack has left.

    Selects exactly those channels, chooses "Delete selected..." and confirms.
    Raises ChannelRackError unless the rack ends up that many channels shorter.
    """
    if platform.system() != "Windows":
        raise ChannelRackError("Deleting channels is only supported on Windows")
    message = fl_windows.open_message()
    if message is not None:
        raise ChannelRackError(
            f"FL Studio is showing a message box ({message!r}); fl_close_message closes it"
        )
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
    _wait_until(lambda: fl_windows.open_message() is None and _count() != before, DELETE_TIMEOUT)
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
