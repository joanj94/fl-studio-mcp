"""Server-side piano roll request queue and response waiting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fl_studio_mcp.tools import piano_roll
from tests.fakes import ToolCollector


def _scripts_dir(settings_dir: Path) -> Path:
    return settings_dir / "Piano roll scripts"


def test_write_request_assigns_ids_and_appends(settings_dir: Path):
    first = piano_roll._write_request({"action": "clear"})
    second = piano_roll._write_request([{"action": "add_notes", "notes": []}])

    queued = json.loads((_scripts_dir(settings_dir) / "mcp_request.json").read_text())
    assert [r["action"] for r in queued] == ["clear", "add_notes"]
    assert [r["id"] for r in queued] == first + second
    assert len(set(first + second)) == 2


def test_write_request_does_not_mutate_caller_dicts(settings_dir: Path):
    request = {"action": "clear"}

    piano_roll._write_request(request)

    assert request == {"action": "clear"}


def test_write_request_recovers_from_corrupt_queue(settings_dir: Path):
    queue = _scripts_dir(settings_dir) / "mcp_request.json"
    queue.parent.mkdir(parents=True)
    queue.write_text("{broken")

    ids = piano_roll._write_request({"action": "clear"})

    assert [r["id"] for r in json.loads(queue.read_text())] == ids


def test_wait_for_response_matches_ids(settings_dir: Path):
    response_file = _scripts_dir(settings_dir) / "mcp_response.json"
    response_file.parent.mkdir(parents=True)
    response_file.write_text(json.dumps({"status": "success", "request_ids": ["a", "b"]}))

    assert piano_roll._wait_for_response(["a"], timeout=0.1)["status"] == "success"


def test_wait_for_response_times_out_on_other_ids(settings_dir: Path):
    response_file = _scripts_dir(settings_dir) / "mcp_response.json"
    response_file.parent.mkdir(parents=True)
    response_file.write_text(json.dumps({"status": "success", "request_ids": ["other"]}))

    assert piano_roll._wait_for_response(["a"], timeout=0.05) is None


class FakeTrigger:
    platform = "Windows"
    keystroke = "Ctrl+Alt+Y"
    is_supported = True

    def __init__(self, on_trigger=None, ok: bool = True) -> None:
        self.on_trigger = on_trigger
        self.ok = ok
        self.delays: list[float] = []

    def trigger(self, delay: float) -> bool:
        self.delays.append(delay)
        if self.on_trigger:
            self.on_trigger()
        return self.ok


def test_trigger_waits_for_fl_instead_of_sleeping(settings_dir: Path, monkeypatch):
    ids = piano_roll._write_request({"action": "clear"})

    def fl_processes_queue() -> None:
        (_scripts_dir(settings_dir) / "mcp_response.json").write_text(
            json.dumps({"status": "success", "request_ids": ids,
                        "notes_added": 0, "notes_deleted": 3})
        )

    trigger = FakeTrigger(on_trigger=fl_processes_queue)
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: trigger)

    info = piano_roll._get_trigger_info(True, ids)

    assert trigger.delays == [0]
    assert "3 deleted" in info


def test_trigger_reports_timeout(settings_dir: Path, monkeypatch):
    ids = piano_roll._write_request({"action": "clear"})
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger())
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.05)

    info = piano_roll._get_trigger_info(True, ids)

    assert "did not respond" in info


def test_trigger_failure_suggests_manual_keystroke(settings_dir: Path, monkeypatch):
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger(ok=False))

    assert "Ctrl+Alt+Y" in piano_roll._get_trigger_info(True, ["x"])


def test_no_auto_trigger_returns_empty(settings_dir: Path):
    assert piano_roll._get_trigger_info(False, ["x"]) == ""


# --- tools -------------------------------------------------------------------


@pytest.fixture
def tools(settings_dir: Path) -> dict:
    collector = ToolCollector()
    piano_roll.register_piano_roll_tools(collector)
    return collector.tools


def _queued(settings_dir: Path) -> list[dict]:
    return json.loads((_scripts_dir(settings_dir) / "mcp_request.json").read_text())


def test_send_notes_validates_fields(tools):
    assert "No notes" in tools["fl_send_notes"]([], auto_trigger=False)
    assert "missing 'midi'" in tools["fl_send_notes"]([{"duration": 1}], auto_trigger=False)
    assert "missing 'duration'" in tools["fl_send_notes"]([{"midi": 60}], auto_trigger=False)


def test_send_notes_replace_queues_clear_first_with_defaults(tools, settings_dir):
    notes = [{"midi": 60 + i, "duration": 1} for i in range(6)]

    summary = tools["fl_send_notes"](notes, mode="replace", auto_trigger=False)

    clear, add = _queued(settings_dir)
    assert clear["action"] == "clear"
    assert add["notes"][0] == {"time": 0, "velocity": 0.8, "midi": 60, "duration": 1}
    assert notes[0] == {"midi": 60, "duration": 1}  # caller's dict untouched
    assert "Queued 6 note(s)" in summary and "1 more" in summary


def test_send_chord(tools, settings_dir):
    assert "No MIDI notes" in tools["fl_send_chord"]([], auto_trigger=False)

    summary = tools["fl_send_chord"]([60, 64, 67], time=2, auto_trigger=False)

    (chord,) = _queued(settings_dir)
    assert chord["action"] == "add_chord" and chord["time"] == 2
    assert "C4, E4, G4" in summary


def test_delete_and_clear(tools, settings_dir):
    assert "No notes" in tools["fl_delete_notes"]([], auto_trigger=False)

    tools["fl_delete_notes"]([{"midi": 60, "time": 0}], auto_trigger=False)
    tools["fl_clear_piano_roll"](auto_trigger=False)

    assert [r["action"] for r in _queued(settings_dir)] == ["delete_notes", "clear"]


def test_clear_request_queue(tools, settings_dir):
    tools["fl_clear_piano_roll"](auto_trigger=False)

    tools["fl_clear_request_queue"]()

    assert not (_scripts_dir(settings_dir) / "mcp_request.json").exists()


def test_get_state_adds_note_names(tools, settings_dir):
    assert "error" in tools["fl_get_piano_roll_state"]()

    state_file = _scripts_dir(settings_dir) / "piano_roll_state.json"
    state_file.write_text(json.dumps({"ppq": 96, "notes": [{"midi": 69}]}))

    assert tools["fl_get_piano_roll_state"]()["notes"][0]["note_name"] == "A4"


def test_trigger_script_waits_for_pending_requests(tools, settings_dir, monkeypatch):
    ids = piano_roll._write_request({"action": "clear"})

    def fl_processes_queue() -> None:
        (_scripts_dir(settings_dir) / "mcp_response.json").write_text(
            json.dumps({"status": "error", "message": "no piano roll", "request_ids": ids})
        )

    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger(fl_processes_queue))

    assert "no piano roll" in tools["fl_trigger_script"]()


def test_real_script_error_reaches_the_tool(
    tools, settings_dir, piano_roll_script, flp, monkeypatch
):
    """End to end: server tool → real ComposeWithLLM script (fake FL) → server."""
    scripts_dir = _scripts_dir(settings_dir)
    for name in ("REQUEST_FILE", "RESPONSE_FILE", "STATE_FILE"):
        filename = Path(getattr(piano_roll_script, name)).name
        monkeypatch.setattr(piano_roll_script, name, str(scripts_dir / filename))

    def broken_delete(index):
        raise RuntimeError("score locked")

    flp.score.addNote(flp.Note())
    monkeypatch.setattr(flp.score, "deleteNote", broken_delete)
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger(piano_roll_script.apply))
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.5)

    result = tools["fl_clear_piano_roll"]()

    assert "score locked" in result


def test_trigger_script_with_empty_queue(tools, monkeypatch):
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger())

    assert "no pending requests" in tools["fl_trigger_script"]()


def test_unsupported_platform(tools, monkeypatch):
    unsupported = FakeTrigger()
    unsupported.is_supported = False
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: unsupported)

    assert "not supported" in tools["fl_trigger_script"]()


def test_piano_roll_info(tools, settings_dir):
    info = tools["fl_get_piano_roll_info"]()

    assert info["request_file"] == str(_scripts_dir(settings_dir) / "mcp_request.json")
    assert info["request_file_exists"] is False
