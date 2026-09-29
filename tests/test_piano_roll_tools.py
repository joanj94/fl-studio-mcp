"""Server-side piano roll request queue and response waiting."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fl_studio_mcp.tools import piano_roll
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector


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


# --- targeting a channel/pattern, note fields, fresh reads ---------------------


@pytest.fixture
def fl_conn(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection()
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


NOTE = {"midi": 60, "duration": 1}


def test_send_notes_selects_channel_then_pattern_before_queueing(tools, settings_dir, fl_conn):
    tools["fl_send_notes"]([NOTE], channel=4, pattern=2, auto_trigger=False)

    assert fl_conn.sent == [("channels.selectOne", {"index": 4}), ("patterns.select", {"index": 2})]
    assert _queued(settings_dir)[0]["action"] == "add_notes"


def test_bad_channel_leaves_the_pattern_alone(tools, settings_dir, fl_conn):
    fl_conn.results["channels.selectOne"] = {"error": "no channel 99"}

    result = tools["fl_send_notes"]([NOTE], channel=99, pattern=5, auto_trigger=False)

    assert "no channel 99" in result
    assert fl_conn.sent == [("channels.selectOne", {"index": 99})]


def test_without_target_nothing_is_selected(tools, settings_dir, fl_conn):
    tools["fl_send_notes"]([NOTE], auto_trigger=False)

    assert fl_conn.sent == []


def test_failed_target_selection_queues_nothing(tools, settings_dir, fl_conn):
    fl_conn.results["channels.selectOne"] = {"error": "no channel 9"}

    result = tools["fl_send_notes"]([NOTE], channel=9, auto_trigger=False)

    assert "no channel 9" in result
    assert not (_scripts_dir(settings_dir) / "mcp_request.json").exists()


@pytest.mark.parametrize("tool, args", [
    ("fl_send_chord", {"midi_notes": [60, 64]}),
    ("fl_delete_notes", {"notes": [{"midi": 60, "time": 0}]}),
    ("fl_clear_piano_roll", {}),
])
def test_other_note_tools_accept_a_target(tools, settings_dir, fl_conn, tool, args):
    tools[tool](**args, channel=1, auto_trigger=False)

    assert fl_conn.sent == [("channels.selectOne", {"index": 1})]


def test_send_notes_passes_note_properties_and_converts_fine_pitch(tools, settings_dir):
    note = {"midi": 60, "duration": 1, "pan": 0.2, "slide": True, "porta": False,
            "fine_pitch": -50, "muted": True, "note_name": "C4", "time_ticks": 0}

    tools["fl_send_notes"]([note], auto_trigger=False)

    (queued,) = _queued(settings_dir)[0]["notes"]
    assert queued == {"midi": 60, "duration": 1, "time": 0, "velocity": 0.8, "pan": 0.2,
                      "slide": True, "porta": False, "pitchofs": -5, "muted": True}


@pytest.mark.parametrize("note", [
    {"midi": 60, "duration": 1, "fine_pitch": 1300},
    {"midi": 60, "duration": 1, "pan": 1.5},
    {"midi": 60, "duration": 1, "slide": "yes"},
])
def test_send_notes_rejects_invalid_note_properties(tools, settings_dir, note):
    assert "Error" in tools["fl_send_notes"]([note], auto_trigger=False)


def test_get_state_of_a_target_reads_fresh_state(tools, settings_dir, fl_conn, monkeypatch):
    def fl_runs_script() -> None:
        ids = [r["id"] for r in _queued(settings_dir)]
        scripts = _scripts_dir(settings_dir)
        (scripts / "piano_roll_state.json").write_text(
            json.dumps({"ppq": 96, "noteCount": 1, "notes": [{"midi": 69}]})
        )
        (scripts / "mcp_response.json").write_text(
            json.dumps({"status": "success", "request_ids": ids})
        )

    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger(fl_runs_script))

    state = tools["fl_get_piano_roll_state"](channel=4)

    assert fl_conn.sent == [("channels.selectOne", {"index": 4})]
    assert state["notes"][0]["note_name"] == "A4"


def test_get_state_reports_when_fl_does_not_answer(tools, settings_dir, fl_conn, monkeypatch):
    monkeypatch.setattr(piano_roll, "get_trigger", lambda: FakeTrigger())
    monkeypatch.setattr(piano_roll, "RESPONSE_TIMEOUT", 0.05)

    assert "did not respond" in tools["fl_get_piano_roll_state"](refresh=True)["error"]


def test_piano_roll_info(tools, settings_dir):
    info = tools["fl_get_piano_roll_info"]()

    assert info["request_file"] == str(_scripts_dir(settings_dir) / "mcp_request.json")
    assert info["request_file_exists"] is False
