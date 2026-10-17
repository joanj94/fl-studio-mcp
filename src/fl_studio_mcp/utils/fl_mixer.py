"""Deleting an effect from a mixer slot, which FL's API can't do.

The mixer shows the selected track's ten effect slots in a list on its right
(`fl_windows.mixer_slot_panel`). A posted click on a filled slot's arrow (its
left end) opens the slot's menu; its second entry is "Delete..." (Home, Down;
seen live), which asks "Confirm" with Ok and Cancel. A letter is no good here:
seen live, D chose "Move down". The result is checked by the track's effects:
the others must be left where they were.
"""

from __future__ import annotations

import platform
import time

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.connection import call

SLOTS = 10
ARROW_X = 9  # px from the slot list's left edge: the slot's arrow
MENU_TIMEOUT = 1.5
CONFIRM_TIMEOUT = 1.5
DELETE_TIMEOUT = 2.0
POLL_INTERVAL = 0.02
SELECT_SETTLE = 0.3  # seconds for the mixer to show the selected track's slots
CONFIRM_TITLE = "Confirm"
OK_BUTTON = 0
DELETE_KEYS = (fl_windows.VK_HOME, fl_windows.VK_DOWN)


class MixerError(Exception):
    """The effect could not be deleted."""


def _fl(action: str, params: dict | None = None) -> dict:
    result = call(action, params)
    if "error" in result:
        raise MixerError(result["error"])
    return result


def _wait_until(condition, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)
    return True


def _effects(track: int) -> dict[int, str]:
    listed = _fl("mixer.getTrackEffects", {"track": track})["effects"]
    return {e["slot"]: e["plugin"] for e in listed}


def _choose_delete(slot: int) -> None:
    found = fl_windows.mixer_slot_panel()
    if found is None:
        raise MixerError("The mixer's effect slots aren't showing (is the mixer detached?)")
    hwnd, height = found
    if fl_windows.popup_menu_count() != 0:
        raise MixerError("FL Studio has a menu open. Close it and try again.")
    fl_windows.click_in_window(hwnd, ARROW_X, int(height / SLOTS * (slot + 0.5)))
    try:
        if not _wait_until(lambda: fl_windows.popup_menu_count() == 1, MENU_TIMEOUT):
            raise MixerError("The effect slot's menu did not open")
        for key in DELETE_KEYS:
            if not fl_windows.press_in_popup_menu(key):
                raise MixerError("The effect slot's menu closed unexpectedly")
        if fl_windows.pointer_over_menu():
            raise MixerError(
                "The mouse pointer is over FL Studio's menu, where it moves the highlight, "
                "so nothing was chosen. Try again with the pointer elsewhere."
            )
        if not fl_windows.press_enter_in_popup_menu():
            raise MixerError("The effect slot's menu did not take the choice")
    finally:
        if fl_windows.popup_menu_count():
            fl_windows.close_popup_menus()


def delete_effect(track: int, slot: int) -> str:
    """Delete the effect in a mixer track's slot (0-9); returns its name. Windows only."""
    if platform.system() != "Windows":
        raise MixerError("Effects can only be deleted on Windows")
    if isinstance(slot, bool) or not isinstance(slot, int) or not 0 <= slot < SLOTS:
        raise MixerError(f"slot must be 0-{SLOTS - 1}, got {slot!r}")
    if fl_windows.open_message() is not None:
        raise MixerError("FL Studio is showing a message box; fl_close_message closes it")
    before = _effects(track)
    if slot not in before:
        raise MixerError(f"Mixer track {track} has no effect in slot {slot}")
    _fl("ui.focusWindow", {"window": "mixer"})
    selected = _fl("mixer.selectTrack", {"track": track})
    try:
        time.sleep(SELECT_SETTLE)
        _choose_delete(slot)
        if _wait_until(lambda: fl_windows.open_message() is not None, CONFIRM_TIMEOUT):
            title = fl_windows.open_message()
            if title != CONFIRM_TITLE or not fl_windows.click_message_button(OK_BUTTON):
                fl_windows.close_messages()
                raise MixerError(f"FL Studio showed {title!r} instead of its delete "
                                 "confirmation; it was closed and nothing was deleted")
        expected = {s: name for s, name in before.items() if s != slot}
        _wait_until(lambda: _effects(track) == expected, DELETE_TIMEOUT)
        if fl_windows.open_message() is not None:  # never leave FL blocked by a box
            fl_windows.close_messages()
        after = _effects(track)
        if after != expected:
            raise MixerError(f"Mixer track {track}'s effects are {after} after deleting slot "
                             f"{slot}, where {expected} were expected")
    finally:
        if selected.get("previous") not in (None, track):
            call("mixer.selectTrack", {"track": selected["previous"]})
    return before[slot]
