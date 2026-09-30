"""Rendering the project to a WAV file through FL Studio's own export dialog.

The scripting API can't export audio, so this drives File > Export by keyboard:
Ctrl+R, the file path in the Save dialog, Enter, then Enter in the render
window. Each window is waited for before the next key, and the render is done
when the render window has closed and the file exists. Windows only.
"""

from __future__ import annotations

import platform
import time
from pathlib import Path
from typing import Any

from fl_studio_mcp.utils import fl_windows
from fl_studio_mcp.utils.fl_trigger import get_trigger, keyboard_lock

WINDOW_TIMEOUT = 5.0  # seconds for a dialog to open
POLL_INTERVAL = 0.1
KEY_DELAY = 0.3


class RenderError(Exception):
    """The render could not be started or did not finish; the message says why."""


def _keyboard() -> tuple[Any, Any]:
    """pynput's keyboard controller and Key enum (imported late: it needs a desktop)."""
    from pynput.keyboard import Controller, Key

    return Controller(), Key


def _wait_for(class_name: str, count: int, timeout: float) -> bool:
    """Wait until FL has exactly `count` windows of a class."""
    deadline = time.monotonic() + timeout
    while True:
        if fl_windows.window_count(class_name) == count:
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)


def _busy() -> str | None:
    """What is in the way of starting a render, or None."""
    if fl_windows.popup_menu_count():
        return "a menu is open in FL Studio"
    if fl_windows.window_count(fl_windows.SYSTEM_DIALOG_CLASS):
        return "a dialog is open in FL Studio"
    if fl_windows.window_count(fl_windows.RENDER_WINDOW_CLASS):
        return "FL Studio's render window is already open"
    return None


def _close_dialogs(keyboard: Any, key: Any) -> None:
    """Esc out of the export dialog or the render window, so FL isn't left blocked."""
    for _ in range(2):
        open_windows = fl_windows.window_count(
            fl_windows.SYSTEM_DIALOG_CLASS
        ) + fl_windows.window_count(fl_windows.RENDER_WINDOW_CLASS)
        if not open_windows or not get_trigger().has_focus():
            return
        keyboard.tap(key.esc)
        time.sleep(KEY_DELAY)


def render_wav(path: Path, timeout: float) -> float:
    """Render what FL would play (pattern or song, per its mode) to `path`.

    `path` must not exist yet, so FL never asks to overwrite. Returns the
    seconds the render took. Raises RenderError with the reason otherwise.
    """
    if platform.system() != "Windows":
        raise RenderError("Rendering by keystrokes is only supported on Windows")
    if path.exists():
        raise RenderError(f"{path} already exists; pick another file name")
    if not path.parent.is_dir():
        raise RenderError(f"The folder {path.parent} does not exist")

    trigger = get_trigger()
    with keyboard_lock:
        if not trigger.focus():
            raise RenderError("FL Studio's window could not be brought to the front")
        busy = _busy()
        if busy:
            raise RenderError(f"Cannot start a render while {busy}")
        try:
            keyboard, key = _keyboard()
            time.sleep(KEY_DELAY)
            with keyboard.pressed(key.ctrl):
                keyboard.tap("r")
            if not _wait_for(fl_windows.SYSTEM_DIALOG_CLASS, 1, WINDOW_TIMEOUT):
                raise RenderError("FL Studio's export dialog did not open")
            time.sleep(KEY_DELAY)
            if not trigger.has_focus():
                raise RenderError("FL Studio lost focus; the export dialog was left open")
            keyboard.type(str(path))
            time.sleep(KEY_DELAY)
            keyboard.tap(key.enter)
            if not _wait_for(fl_windows.RENDER_WINDOW_CLASS, 1, WINDOW_TIMEOUT):
                _close_dialogs(keyboard, key)
                raise RenderError(
                    "FL Studio's render window did not open (is the path one FL can write to?)"
                )
            time.sleep(KEY_DELAY)
            started = time.monotonic()
            keyboard.tap(key.enter)
            if not _wait_for(fl_windows.RENDER_WINDOW_CLASS, 0, timeout):
                raise RenderError(f"The render did not finish within {timeout:.0f} s")
        except RenderError:
            raise
        except Exception as e:
            raise RenderError(f"Could not drive FL Studio's export dialog: {e}") from e

    if not path.is_file():
        raise RenderError(f"FL Studio closed its render window but {path} was not written")
    return time.monotonic() - started
