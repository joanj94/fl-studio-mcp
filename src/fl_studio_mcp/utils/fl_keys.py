"""Keystroke sequences for things FL Studio's scripting API can't do.

FL must be the foreground window; the sequences take over the keyboard for a
moment. Callers confirm the effect through the controller afterwards, since a
keystroke gives no result.
"""

from __future__ import annotations

import time
from typing import Any

from fl_studio_mcp.utils.fl_trigger import get_trigger, keyboard_lock

# Seconds FL needs before the next key lands (measured live, FL 2026).
FOCUS_DELAY = 0.3
PICKER_OPEN_DELAY = 1.0
PICKER_SEARCH_DELAY = 0.6


def _keyboard() -> tuple[Any, Any]:
    """pynput's keyboard controller and Key enum (imported late: it needs a desktop)."""
    from pynput.keyboard import Controller, Key

    return Controller(), Key


def add_generator_keystrokes(name: str) -> bool:
    """Add a channel through the plugin picker: F8, the generator's name, Enter.

    The picker's search is fuzzy and Enter loads its best guess (an effect, if
    nothing else matches), so only pass a name checked with `find_generator`.
    Focus is re-checked before each step, because text typed into another
    program could do anything. Stopping midway can leave the picker open.

    Returns:
        True if all keys were sent to FL Studio, False otherwise.
    """
    trigger = get_trigger()
    with keyboard_lock:
        if not trigger.focus():
            return False
        try:
            keyboard, key = _keyboard()
            time.sleep(FOCUS_DELAY)
            if not trigger.has_focus():
                return False
            keyboard.tap(key.f8)
            time.sleep(PICKER_OPEN_DELAY)
            if not trigger.has_focus():
                return False
            keyboard.type(name.lower())  # the search ignores case; lowercase needs no Shift
            time.sleep(PICKER_SEARCH_DELAY)
            if not trigger.has_focus():
                return False
            keyboard.tap(key.enter)
        except Exception:
            return False
    return True
