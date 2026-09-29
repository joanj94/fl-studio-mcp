"""FL-side MIDI controller script: command execution and response protocol."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

TRIGGER = SimpleNamespace(midiId=0x90, data1=127, data2=127, handled=False)


@pytest.fixture(autouse=True)
def _song_length(fl_modules):
    fl_modules["transport"].getSongLength.return_value = 1000


def _run(controller, command: dict | str) -> dict:
    text = command if isinstance(command, str) else json.dumps(command)
    controller.COMMAND_FILE.write_text(text)
    controller.execute_pending_command()
    return json.loads(controller.RESPONSE_FILE.read_text())


def test_known_action_succeeds_and_calls_fl_api(controller, fl_modules):
    fl_modules["mixer"].getTrackVolume.return_value = 0.5

    response = _run(
        controller,
        {"id": "abc", "action": "mixer.setTrackVolume", "params": {"track": 3, "volume": 0.5}},
    )

    fl_modules["mixer"].setTrackVolume.assert_called_once_with(3, 0.5)
    assert response["success"] is True
    assert response["volume"] == 0.5


def test_response_echoes_request_id(controller):
    response = _run(controller, {"id": "req-42", "action": "transport.getLength"})

    assert response["id"] == "req-42"


def test_unknown_action_is_reported_as_failure(controller):
    response = _run(controller, {"id": "x", "action": "nope.nothing"})

    assert response["success"] is False
    assert "Unknown action" in response["error"]
    assert response["id"] == "x"


def test_handler_exception_is_reported_as_failure(controller, fl_modules):
    fl_modules["mixer"].setTrackVolume.side_effect = RuntimeError("boom")

    response = _run(controller, {"id": "x", "action": "mixer.setTrackVolume", "params": {}})

    assert response["success"] is False
    assert "boom" in response["error"]
    assert response["id"] == "x"


def test_unserializable_result_still_produces_a_response(controller, fl_modules):
    fl_modules["transport"].getSongLength.return_value = object()

    response = _run(controller, {"id": "x", "action": "transport.getLength"})

    assert response["success"] is False
    assert response["id"] == "x"
    assert "serialize" in response["error"]


def test_invalid_json_is_reported_as_failure(controller):
    response = _run(controller, "{not json")

    assert response["success"] is False
    assert "Invalid JSON" in response["error"]


def test_missing_command_file_is_reported(controller):
    controller.execute_pending_command()

    response = json.loads(controller.RESPONSE_FILE.read_text())
    assert response["success"] is False
    assert "No command file" in response["error"]


def test_response_write_leaves_no_temp_file(controller):
    _run(controller, {"id": "x", "action": "transport.getLength"})

    leftovers = [p.name for p in controller.RESPONSE_FILE.parent.iterdir()]
    assert sorted(leftovers) == ["mcp_command.json", "mcp_response.json"]


def test_response_write_never_renames(controller, monkeypatch):
    # FL Studio 2026's os.replace returns NULL without an exception and leaves the
    # embedded interpreter broken for every later call (seen live), so never call it.
    calls = []
    monkeypatch.setattr(controller.os, "replace", lambda *args: calls.append(args))
    monkeypatch.setattr(controller.os, "remove", lambda *args: calls.append(args))
    monkeypatch.setattr(controller.os, "unlink", lambda *args: calls.append(args))

    response = _run(controller, {"id": "fl", "action": "transport.getLength"})

    assert response["id"] == "fl"
    assert calls == []


def test_trigger_note_executes_command(controller):
    controller.COMMAND_FILE.write_text(json.dumps({"id": "t", "action": "transport.getLength"}))
    event = SimpleNamespace(**vars(TRIGGER))

    controller.OnMidiMsg(event)

    assert event.handled is True
    assert json.loads(controller.RESPONSE_FILE.read_text())["id"] == "t"


def test_other_midi_notes_are_ignored(controller):
    event = SimpleNamespace(midiId=0x90, data1=60, data2=100, handled=False)

    controller.OnMidiMsg(event)

    assert event.handled is False
    assert not controller.RESPONSE_FILE.exists()


def test_script_dir_uses_resolved_documents_folder(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "_get_documents_dir", lambda: tmp_path / "OneDrive" / "Docs")

    script_dir = controller._get_script_dir()

    expected = tmp_path / "OneDrive" / "Docs" / "Image-Line" / "FL Studio" / "Settings"
    assert script_dir == expected / "Hardware" / "FLStudioMCP"
