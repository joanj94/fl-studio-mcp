"""MIDI-based connection to FL Studio.

This module provides communication with FL Studio via MIDI messages and JSON files.
The approach is:
1. Write command data (with a unique id) to a JSON file
2. Send a MIDI trigger note to FL Studio
3. FL Studio's MIDI controller script executes the command
4. Read the response whose id matches from a JSON file

This is similar to how the piano_roll module works, but uses MIDI for triggering
instead of keystrokes.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fl_studio_mcp.utils.paths import atomic_write_json, get_hardware_dir

MIDI_PORT_ENV = "FL_MCP_MIDI_PORT"

# Substrings identifying virtual MIDI ports (macOS IAC Driver, Windows loopMIDI).
VIRTUAL_PORT_HINTS = ("IAC", "loopMIDI", "FL")

POLL_INTERVAL = 0.02  # seconds between response file checks


class PortSelectionError(ValueError):
    """No suitable MIDI output port could be chosen."""


def select_output_port(available: list[str], requested: str | None) -> str:
    """Choose the MIDI output port to talk to FL Studio through.

    Args:
        available: Output port names reported by the MIDI backend.
        requested: Port name or case-insensitive substring (from FL_MCP_MIDI_PORT).

    Raises:
        PortSelectionError: If nothing matches. Never falls back to an arbitrary
            port, since that could be a hardware synth rather than FL Studio.
    """
    listing = ", ".join(available) or "none"

    if requested:
        if requested in available:
            return requested
        needle = requested.lower()
        for name in available:
            if needle in name.lower():
                return name
        raise PortSelectionError(
            f"MIDI port '{requested}' ({MIDI_PORT_ENV}) not found. Available ports: {listing}"
        )

    for name in available:
        if any(hint.upper() in name.upper() for hint in VIRTUAL_PORT_HINTS):
            return name

    raise PortSelectionError(
        "No virtual MIDI port found (expected loopMIDI on Windows or IAC Driver on macOS). "
        f"Available ports: {listing}. Set {MIDI_PORT_ENV} to pick one explicitly."
    )


def _mido_list_ports() -> list[str]:
    import mido

    return mido.get_output_names()


def _mido_open_port(name: str) -> Any:
    import mido

    return mido.open_output(name)


def _trigger_message(note: int) -> Any:
    import mido

    return mido.Message("note_on", note=note, velocity=127)


class MIDIConnection:
    """MIDI-based connection to FL Studio.

    Communicates with FL Studio via:
    - JSON files for command/response data
    - MIDI trigger note to execute commands
    """

    # MIDI trigger note (same as in FL Studio controller script)
    TRIGGER_NOTE = 127

    def __init__(
        self,
        hardware_dir: Path | None = None,
        list_ports: Callable[[], list[str]] = _mido_list_ports,
        open_port: Callable[[str], Any] = _mido_open_port,
    ) -> None:
        self._list_ports = list_ports
        self._open_port = open_port
        self._port = None
        self._port_name: str | None = None
        self._connected = False
        self._error: str | None = None
        # One command in flight at a time: FL reads a single command file.
        self._lock = threading.Lock()

        # File paths for JSON communication
        self._hardware_dir = hardware_dir or get_hardware_dir()
        self._command_file = self._hardware_dir / "mcp_command.json"
        self._response_file = self._hardware_dir / "mcp_response.json"

    @property
    def is_connected(self) -> bool:
        """Check if connected to FL Studio."""
        return self._connected and self._port is not None

    @property
    def connection_error(self) -> str | None:
        """Get the last connection error, if any."""
        return self._error

    def connect(self) -> bool:
        """Attempt to connect to FL Studio via MIDI.

        Returns True if connection successful, False otherwise.
        """
        if self.is_connected:
            return True

        try:
            available = self._list_ports()
        except ImportError:
            self._error = "mido library not installed. Install with: pip install mido python-rtmidi"
            return False
        except Exception as e:
            self._error = f"Failed to get MIDI ports: {e}"
            return False

        try:
            target_port = select_output_port(available, os.environ.get(MIDI_PORT_ENV))
        except PortSelectionError as e:
            self._error = str(e)
            return False

        try:
            self._port = self._open_port(target_port)
        except Exception as e:
            self._error = f"Failed to open MIDI port '{target_port}': {e}"
            return False

        self._port_name = target_port
        self._connected = True
        self._error = None
        return True

    def disconnect(self) -> None:
        """Close the MIDI connection."""
        if self._port is not None:
            try:
                self._port.close()
            except Exception:
                pass  # Closing a dead port is best-effort; state is reset below.
            self._port = None
        self._connected = False
        self._port_name = None

    def ensure_connected(self) -> None:
        """Ensure connection to FL Studio is active. Raises RuntimeError if not."""
        if not self.connect():
            raise RuntimeError(self._error or "Failed to connect to FL Studio via MIDI")

    def send_command(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float = 2.0,
    ) -> dict[str, Any]:
        """Send a command to FL Studio and wait for response.

        Args:
            action: The command action (e.g., "transport.start", "mixer.setTrackVolume")
            params: Optional parameters for the command
            timeout: Maximum time to wait for response in seconds

        Returns:
            Response dictionary from FL Studio

        Raises:
            RuntimeError: If not connected
        """
        self.ensure_connected()

        request_id = uuid.uuid4().hex
        command = {"id": request_id, "action": action, "params": params or {}}

        with self._lock:
            self._discard_response_file()

            try:
                atomic_write_json(self._command_file, command)
            except OSError as e:
                return {"success": False, "error": f"Failed to write command file: {e}"}

            try:
                self._port.send(_trigger_message(self.TRIGGER_NOTE))
            except Exception as e:
                return {"success": False, "error": f"Failed to send MIDI trigger: {e}"}

            return self._wait_for_response(request_id, timeout)

    def _discard_response_file(self) -> None:
        try:
            self._response_file.unlink()
        except FileNotFoundError:
            pass

    def _read_response(self) -> dict[str, Any] | None:
        """Read the response file, or None if it is missing or still being written."""
        try:
            return json.loads(self._response_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    def _wait_for_response(self, request_id: str, timeout: float) -> dict[str, Any]:
        """Wait for a response carrying request_id; responses to other ids are ignored."""
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            response = self._read_response()
            if response is not None and response.get("id") == request_id:
                self._discard_response_file()
                return response
            time.sleep(POLL_INTERVAL)

        return {
            "success": False,
            "error": (
                f"Timeout waiting for FL Studio response after {timeout}s. "
                "Make sure FL Studio is running and the MCP controller is enabled "
                "in MIDI Settings."
            ),
        }

    def get_status(self) -> dict[str, Any]:
        """Get connection status information."""
        try:
            output_ports = self._list_ports()
        except Exception:
            output_ports = []

        return {
            "connected": self.is_connected,
            "port_name": self._port_name,
            "available_ports": output_ports,
            "command_file": str(self._command_file),
            "response_file": str(self._response_file),
            "error": self._error,
        }


# Global connection instance
_connection: MIDIConnection | None = None


def get_connection() -> MIDIConnection:
    """Get the global MIDI connection instance."""
    global _connection
    if _connection is None:
        _connection = MIDIConnection()
    return _connection


def reset_connection() -> None:
    """Reset the connection state to allow reconnection attempts."""
    global _connection
    if _connection is not None:
        _connection.disconnect()
        _connection = None
