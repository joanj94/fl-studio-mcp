"""Server-side MIDI connection: port selection and the command/response protocol."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fl_studio_mcp.utils.midi_connection import (
    MIDIConnection,
    PortSelectionError,
    select_output_port,
)

# --- port selection ---------------------------------------------------------


def test_requested_port_exact_match():
    assert select_output_port(["A", "My Port"], "My Port") == "My Port"


def test_requested_port_substring_is_case_insensitive():
    assert select_output_port(["Keyboard", "loopMIDI Port 1"], "loopmidi") == "loopMIDI Port 1"


def test_requested_port_missing_lists_available():
    with pytest.raises(PortSelectionError) as err:
        select_output_port(["Keyboard"], "loopMIDI")
    assert "Keyboard" in str(err.value)


def test_auto_selects_virtual_port():
    assert select_output_port(["Keyboard", "loopMIDI Port 1"], None) == "loopMIDI Port 1"
    assert select_output_port(["IAC Driver Bus 1"], None) == "IAC Driver Bus 1"


def test_auto_select_never_falls_back_to_hardware_port():
    with pytest.raises(PortSelectionError) as err:
        select_output_port(["Microsoft GS Wavetable Synth", "Keyboard"], None)
    assert "FL_MCP_MIDI_PORT" in str(err.value)


def test_no_ports_at_all():
    with pytest.raises(PortSelectionError):
        select_output_port([], None)


# --- command / response protocol ---------------------------------------------


class FakeFL:
    """A MIDI output port that behaves like FL's controller script on trigger."""

    def __init__(self, hardware_dir: Path, reply_id: str | None = "echo") -> None:
        self.hardware_dir = hardware_dir
        self.reply_id = reply_id
        self.sent: list = []
        self.closed = False

    def send(self, message) -> None:
        self.sent.append(message)
        command = json.loads((self.hardware_dir / "mcp_command.json").read_text())
        reply_id = command["id"] if self.reply_id == "echo" else self.reply_id
        response = {"success": True, "id": reply_id, "action_seen": command["action"]}
        (self.hardware_dir / "mcp_response.json").write_text(json.dumps(response))

    def close(self) -> None:
        self.closed = True


def _connection(tmp_path: Path, port: FakeFL, ports: list[str] | None = None) -> MIDIConnection:
    return MIDIConnection(
        hardware_dir=tmp_path,
        list_ports=lambda: ports if ports is not None else ["loopMIDI Port 1"],
        open_port=lambda name: port,
    )


def test_send_command_round_trip(tmp_path: Path):
    port = FakeFL(tmp_path)
    conn = _connection(tmp_path, port)

    result = conn.send_command("mixer.getTrackCount", {"x": 1})

    assert result["success"] is True
    assert result["action_seen"] == "mixer.getTrackCount"
    assert port.sent[0].note == MIDIConnection.TRIGGER_NOTE


def test_command_file_carries_unique_id(tmp_path: Path):
    conn = _connection(tmp_path, FakeFL(tmp_path))

    conn.send_command("a.b")
    first = json.loads((tmp_path / "mcp_command.json").read_text())
    conn.send_command("a.b")
    second = json.loads((tmp_path / "mcp_command.json").read_text())

    assert first["id"] and second["id"] and first["id"] != second["id"]
    assert first["params"] == {}


def test_response_with_wrong_id_is_ignored(tmp_path: Path):
    conn = _connection(tmp_path, FakeFL(tmp_path, reply_id="stale"))

    result = conn.send_command("a.b", timeout=0.1)

    assert result["success"] is False
    assert "Timeout" in result["error"]


def test_old_response_file_is_removed_before_sending(tmp_path: Path):
    (tmp_path / "mcp_response.json").write_text(json.dumps({"success": True, "id": "old"}))
    port = FakeFL(tmp_path, reply_id=None)
    port.send = lambda message: None  # FL never answers
    conn = _connection(tmp_path, port)

    result = conn.send_command("a.b", timeout=0.05)

    assert result["success"] is False
    assert not (tmp_path / "mcp_response.json").exists()


def test_connect_uses_env_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    opened: list[str] = []
    monkeypatch.setenv("FL_MCP_MIDI_PORT", "Custom")
    conn = MIDIConnection(
        hardware_dir=tmp_path,
        list_ports=lambda: ["loopMIDI Port 1", "Custom Bus"],
        open_port=lambda name: opened.append(name) or FakeFL(tmp_path),
    )

    assert conn.connect() is True
    assert opened == ["Custom Bus"]
    assert conn.get_status()["port_name"] == "Custom Bus"


def test_connect_reports_selection_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    conn = _connection(tmp_path, FakeFL(tmp_path), ports=["Keyboard"])

    assert conn.connect() is False
    assert "FL_MCP_MIDI_PORT" in conn.connection_error
    with pytest.raises(RuntimeError):
        conn.send_command("a.b")


def test_disconnect_closes_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("FL_MCP_MIDI_PORT", raising=False)
    port = FakeFL(tmp_path)
    conn = _connection(tmp_path, port)
    conn.connect()

    conn.disconnect()

    assert port.closed is True
    assert conn.is_connected is False
