"""FL Studio trigger utility for Piano Roll script execution.

This module handles sending keystrokes to FL Studio to trigger the
ComposeWithLLM Piano Roll script, which processes JSON note requests.
"""

from __future__ import annotations

import platform
import subprocess
import threading
import time
from typing import Callable

# Delay after triggering to allow FL Studio to process
TRIGGER_DELAY = 2.0

# Held while keys are being sent to FL Studio, so two tools never interleave
# their keystrokes. Re-entrant: a tool holding it may call trigger().
keyboard_lock = threading.RLock()


class FLStudioTrigger:
    """Handles triggering FL Studio's Piano Roll script via keystrokes.

    The trigger sends Cmd+Opt+Y (macOS) or Ctrl+Alt+Y (Windows) to FL Studio,
    which executes the ComposeWithLLM.pyscript to process pending JSON requests.
    """

    def __init__(self) -> None:
        self._system = platform.system()
        self._trigger_func: Callable[[], bool] | None = None
        self._setup_trigger()

    def _setup_trigger(self) -> None:
        """Set up the appropriate trigger method for the current platform."""
        if self._system == "Darwin":
            self._trigger_func = self._trigger_macos
        elif self._system == "Windows":
            self._trigger_func = self._trigger_windows
        else:
            self._trigger_func = None

    def _trigger_macos(self) -> bool:
        """Trigger FL Studio on macOS using osascript."""
        try:
            # Use osascript to send keystroke to FL Studio
            script = '''
            tell application "FL Studio"
                activate
            end tell
            delay 0.3
            tell application "System Events"
                keystroke "y" using {command down, option down}
            end tell
            '''
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                timeout=10,
            )
            return True
        except subprocess.TimeoutExpired:
            return False
        except Exception:
            # Fallback to pynput
            return self._trigger_macos_pynput()

    def _trigger_macos_pynput(self) -> bool:
        """Trigger FL Studio on macOS using pynput."""
        try:
            from pynput.keyboard import Controller, Key

            keyboard = Controller()

            # Focus FL Studio first
            subprocess.run(
                ["osascript", "-e", 'tell application "FL Studio" to activate'],
                capture_output=True,
                timeout=5,
            )
            time.sleep(0.3)

            # Send Cmd+Opt+Y
            keyboard.press(Key.cmd)
            keyboard.press(Key.alt)
            keyboard.press("y")
            keyboard.release("y")
            keyboard.release(Key.alt)
            keyboard.release(Key.cmd)

            return True
        except Exception:
            return False

    def _focus_fl_studio_windows(self) -> bool:
        """Bring the FL Studio window to the foreground on Windows.

        Keystrokes are delivered to whichever window has focus, so this only
        returns True once Windows confirms FL Studio is the foreground window
        (see ``utils/win_focus.py``).
        """
        try:
            from fl_studio_mcp.utils.win_focus import focus_fl_studio

            return focus_fl_studio()
        except Exception:
            return False

    def _focus_fl_studio_macos(self) -> bool:
        """Bring FL Studio to the foreground on macOS using osascript."""
        try:
            done = subprocess.run(
                ["osascript", "-e", 'tell application "FL Studio" to activate'],
                capture_output=True,
                timeout=5,
            )
            return done.returncode == 0
        except Exception:
            return False

    def has_focus(self) -> bool:
        """Whether FL Studio is the foreground window right now.

        Only Windows can be asked; elsewhere this trusts the last focus() call.
        """
        if self._system != "Windows":
            return True
        try:
            from fl_studio_mcp.utils.win_focus import fl_studio_is_focused

            return fl_studio_is_focused()
        except Exception:
            return False

    def focus(self) -> bool:
        """Bring FL Studio to the foreground so keystrokes reach it.

        Returns:
            True if FL Studio was foregrounded, False if it wasn't found or the
            platform isn't supported.
        """
        if self._system == "Windows":
            return self._focus_fl_studio_windows()
        if self._system == "Darwin":
            return self._focus_fl_studio_macos()
        return False

    def _trigger_windows(self) -> bool:
        """Trigger FL Studio on Windows using pynput.

        Foregrounds FL Studio first so the Ctrl+Alt+Y hotkey actually reaches
        it; returns False (so the caller reports a manual-trigger warning) if the
        FL Studio window can't be found.
        """
        try:
            from pynput.keyboard import Controller, Key

            if not self._focus_fl_studio_windows():
                return False

            # Give FL Studio a moment to accept focus before the keystroke.
            time.sleep(0.3)

            keyboard = Controller()

            # Send Ctrl+Alt+Y
            keyboard.press(Key.ctrl)
            keyboard.press(Key.alt)
            keyboard.press("y")
            keyboard.release("y")
            keyboard.release(Key.alt)
            keyboard.release(Key.ctrl)

            return True
        except Exception:
            return False

    def trigger(self, delay: float = TRIGGER_DELAY) -> bool:
        """Trigger FL Studio to execute the Piano Roll script.

        Args:
            delay: Seconds to wait after triggering for FL Studio to process.

        Returns:
            True if the trigger was sent successfully, False otherwise.
        """
        if self._trigger_func is None:
            return False

        with keyboard_lock:
            success = self._trigger_func()
        if success and delay > 0:
            time.sleep(delay)

        return success

    @property
    def is_supported(self) -> bool:
        """Check if triggering is supported on this platform."""
        return self._trigger_func is not None

    @property
    def platform(self) -> str:
        """Get the current platform name."""
        return self._system

    @property
    def keystroke(self) -> str:
        """Get the keystroke used for this platform."""
        if self._system == "Darwin":
            return "Cmd+Opt+Y"
        elif self._system == "Windows":
            return "Ctrl+Alt+Y"
        return "Unknown"


# Global trigger instance
_trigger: FLStudioTrigger | None = None


def get_trigger() -> FLStudioTrigger:
    """Get the global FL Studio trigger instance."""
    global _trigger
    if _trigger is None:
        _trigger = FLStudioTrigger()
    return _trigger


def trigger_fl_studio(delay: float = TRIGGER_DELAY) -> bool:
    """Convenience function to trigger FL Studio.

    Args:
        delay: Seconds to wait after triggering.

    Returns:
        True if successful, False otherwise.
    """
    return get_trigger().trigger(delay)
