"""Piano Roll tools for FL Studio - persistent note placement.

This module provides tools for creating, editing, and deleting notes in
FL Studio's piano roll. Unlike MIDI real-time note triggering, these
tools create persistent notes by communicating with FL Studio's Piano Roll
scripting API via JSON files.

Communication flow:
1. MCP server appends requests (each with a unique id) to mcp_request.json
2. Keystroke trigger (Cmd+Opt+Y) executes FL Studio's ComposeWithLLM script
3. Script reads JSON, modifies piano roll, exports state to piano_roll_state.json
4. Script writes mcp_response.json listing the request ids it processed
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.fl_trigger import get_trigger
from fl_studio_mcp.utils.paths import atomic_write_json, get_piano_roll_scripts_dir

if TYPE_CHECKING:
    from fastmcp import FastMCP

# How long to wait for the piano roll script to confirm it processed our requests.
RESPONSE_TIMEOUT = 5.0
POLL_INTERVAL = 0.05

# Serializes read-modify-write of the request queue within this process.
_queue_lock = threading.Lock()


def _get_fl_scripts_dir() -> Path:
    """Get the FL Studio Piano Roll scripts directory."""
    return get_piano_roll_scripts_dir()


def _get_request_file() -> Path:
    """Get the path to the MCP request JSON file."""
    return _get_fl_scripts_dir() / "mcp_request.json"


def _get_response_file() -> Path:
    """Get the path to the MCP response JSON file."""
    return _get_fl_scripts_dir() / "mcp_response.json"


def _get_state_file() -> Path:
    """Get the path to the piano roll state JSON file."""
    return _get_fl_scripts_dir() / "piano_roll_state.json"


def _read_json(path: Path) -> object | None:
    """Read a JSON file, or None if it is missing, locked, or malformed."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, PermissionError, json.JSONDecodeError):
        return None


def _read_queue() -> list[dict]:
    data = _read_json(_get_request_file())
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def _write_request(request: dict | list[dict]) -> list[str]:
    """Append request(s) to the queue, tagging each with a new id.

    Returns:
        The ids assigned, in order; the piano roll script echoes them back.
    """
    new_requests = request if isinstance(request, list) else [request]
    tagged = [{**r, "id": uuid.uuid4().hex} for r in new_requests]

    with _queue_lock:
        atomic_write_json(_get_request_file(), [*_read_queue(), *tagged])

    return [r["id"] for r in tagged]


def _pending_request_ids() -> list[str]:
    return [r["id"] for r in _read_queue() if isinstance(r, dict) and r.get("id")]


def _wait_for_response(request_ids: list[str], timeout: float) -> dict | None:
    """Wait until the piano roll script reports it processed all request_ids."""
    wanted = set(request_ids)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        response = _read_json(_get_response_file())
        if isinstance(response, dict) and wanted <= set(response.get("request_ids", [])):
            return response
        time.sleep(POLL_INTERVAL)

    return None


def _clear_request_file() -> None:
    """Clear the request file."""
    request_file = _get_request_file()
    if request_file.exists():
        request_file.unlink()


def _read_state() -> dict | None:
    """Read the current piano roll state."""
    state_file = _get_state_file()
    if not state_file.exists():
        return None

    try:
        with open(state_file) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None


def _midi_to_note_name(midi: int) -> str:
    """Convert MIDI note number to note name."""
    note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    note_name = note_names[midi % 12]
    octave = (midi // 12) - 1
    return f"{note_name}{octave}"


def _describe_response(response: dict) -> str:
    if response.get("status") == "error":
        return f" FL Studio reported an error: {response.get('message', 'unknown error')}."
    return (
        " FL Studio processed the request: "
        f"{response.get('notes_added', 0)} added, {response.get('notes_deleted', 0)} deleted."
    )


def _trigger_and_wait(request_ids: list[str]) -> str:
    """Trigger the piano roll script and wait for it to confirm request_ids."""
    trigger = get_trigger()
    if not trigger.is_supported:
        return f" Auto-trigger not supported on {trigger.platform}. Press the trigger key manually."
    if not trigger.trigger(0):
        return f" Warning: Could not trigger FL Studio. Press {trigger.keystroke} manually."
    if not request_ids:
        return " FL Studio triggered (no pending requests)."

    response = _wait_for_response(request_ids, RESPONSE_TIMEOUT)
    if response is None:
        return (
            f" Warning: FL Studio did not respond within {RESPONSE_TIMEOUT}s. Make sure a "
            "piano roll is open and ComposeWithLLM was run once from the piano roll's own "
            "menu (Tools > Scripting); "
            f"the requests stay queued, so press {trigger.keystroke} to retry."
        )
    return _describe_response(response)


def _get_trigger_info(auto_trigger: bool, request_ids: list[str]) -> str:
    """Attempt to trigger FL Studio and return a status suffix string."""
    if not auto_trigger:
        return ""
    return _trigger_and_wait(request_ids)


def register_piano_roll_tools(mcp: FastMCP) -> None:
    """Register piano roll tools with the MCP server."""

    @mcp.tool()
    def fl_send_notes(
        notes: list[dict],
        mode: str = "add",
        auto_trigger: bool = True
    ) -> str:
        """Add or replace notes in the FL Studio piano roll.

        This creates persistent notes in the currently open piano roll pattern.
        Notes use quarter-note timing for simplicity.

        Args:
            notes: List of note objects with properties:
                   - midi (int): MIDI note number (60 = C4/Middle C)
                   - duration (float): Length in quarter notes (1.0 = quarter note)
                   - time (float, optional): Start position in quarter notes (default 0)
                   - velocity (float, optional): Velocity 0.0-1.0 (default 0.8)
            mode: "add" to add notes, "replace" to clear existing notes first
            auto_trigger: Whether to automatically trigger FL Studio (default True)

        Example notes:
            [
                {"midi": 60, "duration": 1.0, "time": 0},      # C4 quarter note at beat 0
                {"midi": 64, "duration": 1.0, "time": 0},      # E4 (chord with C4)
                {"midi": 67, "duration": 1.0, "time": 0},      # G4 (C major chord)
                {"midi": 60, "duration": 0.5, "time": 1.0},    # C4 eighth note at beat 1
            ]
        """
        if not notes:
            return "Error: No notes provided"

        # Validate notes
        for i, note in enumerate(notes):
            if "midi" not in note:
                return f"Error: Note {i} missing 'midi' field"
            if "duration" not in note:
                return f"Error: Note {i} missing 'duration' field"

        # Apply defaults without modifying the caller's dicts
        notes = [{"time": 0, "velocity": 0.8, **note} for note in notes]

        clear_first = [{"action": "clear"}] if mode == "replace" else []
        requests = [*clear_first, {"action": "add_notes", "notes": notes}]

        request_ids = _write_request(requests)

        trigger_info = _get_trigger_info(auto_trigger, request_ids)
        note_count = len(notes)
        note_summary = ", ".join(
            f"{_midi_to_note_name(n['midi'])}@{n.get('time', 0)}"
            for n in notes[:5]
        )
        if note_count > 5:
            note_summary += f", ... ({note_count - 5} more)"

        return f"Queued {note_count} note(s): {note_summary}.{trigger_info}"

    @mcp.tool()
    def fl_send_chord(
        midi_notes: list[int],
        time: float = 0,
        duration: float = 1.0,
        velocity: float = 0.8,
        auto_trigger: bool = True
    ) -> str:
        """Add a chord (multiple simultaneous notes) to the FL Studio piano roll.

        This is a convenience function for adding multiple notes at the same time
        with the same duration. For more control, use fl_send_notes.

        Args:
            midi_notes: List of MIDI note numbers (e.g., [60, 64, 67] for C major)
            time: Start position in quarter notes (default 0)
            duration: Length in quarter notes for all notes (default 1.0)
            velocity: Velocity 0.0-1.0 for all notes (default 0.8)
            auto_trigger: Whether to automatically trigger FL Studio

        Example - C major chord at beat 0:
            fl_send_chord([60, 64, 67], time=0, duration=1.0)

        Example - Am7 chord at beat 2:
            fl_send_chord([57, 60, 64, 67], time=2, duration=2.0)
        """
        if not midi_notes:
            return "Error: No MIDI notes provided"

        # Build chord notes with velocity included
        chord_notes = [
            {"midi": midi, "velocity": velocity}
            for midi in midi_notes
        ]

        request = {
            "action": "add_chord",
            "time": time,
            "duration": duration,
            "notes": chord_notes
        }

        request_ids = _write_request(request)

        trigger_info = _get_trigger_info(auto_trigger, request_ids)
        note_names =", ".join(_midi_to_note_name(n) for n in midi_notes)
        return f"Queued chord [{note_names}] at beat {time}, duration {duration}.{trigger_info}"

    @mcp.tool()
    def fl_delete_notes(
        notes: list[dict],
        auto_trigger: bool = True
    ) -> str:
        """Delete specific notes from the FL Studio piano roll.

        Args:
            notes: List of notes to delete, matching by midi and time:
                   - midi (int): MIDI note number
                   - time (float): Start position in quarter notes
            auto_trigger: Whether to automatically trigger FL Studio

        Example:
            [{"midi": 60, "time": 0}, {"midi": 64, "time": 0}]
        """
        if not notes:
            return "Error: No notes specified for deletion"

        request = {
            "action": "delete_notes",
            "notes": notes
        }
        request_ids = _write_request(request)

        trigger_info = _get_trigger_info(auto_trigger, request_ids)
        return f"Queued deletion of {len(notes)} note(s).{trigger_info}"

    @mcp.tool()
    def fl_clear_piano_roll(auto_trigger: bool = True) -> str:
        """Clear all notes from the FL Studio piano roll.

        Args:
            auto_trigger: Whether to automatically trigger FL Studio
        """
        request_ids = _write_request({"action": "clear"})

        trigger_info = _get_trigger_info(auto_trigger, request_ids)
        return f"Queued clear all notes.{trigger_info}"

    @mcp.tool()
    def fl_get_piano_roll_state() -> dict:
        """Get the current state of notes in the FL Studio piano roll.

        Returns a dictionary containing:
        - ppq: Pulses per quarter note (ticks per beat)
        - notes: List of all notes with their properties

        Note: This reads from the last exported state. Trigger FL Studio
        (Cmd+Opt+Y on macOS) to refresh the state file after making changes.
        """
        state = _read_state()

        if state is None:
            return {
                "error": "No piano roll state available. Make sure FL Studio's "
                         "ComposeWithLLM script has been run at least once."
            }

        # Add human-readable note names
        if "notes" in state:
            for note in state["notes"]:
                if "midi" in note:
                    note["note_name"] = _midi_to_note_name(note["midi"])

        return state

    @mcp.tool()
    def fl_clear_request_queue() -> str:
        """Clear any pending note requests without executing them.

        Use this if you want to cancel queued changes before triggering FL Studio.
        """
        _clear_request_file()
        return "Request queue cleared."

    @mcp.tool()
    def fl_trigger_script() -> str:
        """Manually trigger FL Studio to process pending note requests.

        This sends the keystroke (Cmd+Opt+Y on macOS, Ctrl+Alt+Y on Windows)
        to FL Studio to execute the ComposeWithLLM piano roll script.
        """
        return _trigger_and_wait(_pending_request_ids()).strip()

    @mcp.tool()
    def fl_get_piano_roll_info() -> dict:
        """Get information about the Piano Roll integration status.

        Returns platform info, file paths, and whether auto-triggering is supported.
        """
        trigger = get_trigger()

        return {
            "platform": trigger.platform,
            "auto_trigger_supported": trigger.is_supported,
            "trigger_keystroke": trigger.keystroke,
            "scripts_dir": str(_get_fl_scripts_dir()),
            "request_file": str(_get_request_file()),
            "state_file": str(_get_state_file()),
            "request_file_exists": _get_request_file().exists(),
            "state_file_exists": _get_state_file().exists(),
        }
