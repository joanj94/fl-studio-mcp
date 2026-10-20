"""Piano roll requests run through FL's own script runner (no menu walk)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

from fl_studio_mcp.tools import piano_roll
from fl_studio_mcp.utils import gopher
from tests.fakes import FakeNote, make_flpianoroll

SCRIPT = Path(__file__).parent.parent / "scripts" / "ComposeWithLLM.pyscript"


@pytest.fixture
def fl_runner(settings_dir: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """FL's script runner, played by exec() of the sent source against a fake score."""
    scripts = settings_dir / "Piano roll scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    shutil.copy(SCRIPT, scripts / "ComposeWithLLM.pyscript")
    flp = make_flpianoroll()
    monkeypatch.setitem(sys.modules, "flpianoroll", flp)
    state = {"score": flp.score, "runs": 0, "reachable": True, "fail_after": None}

    def run(source: str, returns: str | None = None, timeout: float = 0) -> object:
        if state["fail_after"] is not None and state["runs"] >= state["fail_after"]:
            raise gopher.GopherError("The connection to FL's Gopher page was closed")
        state["runs"] += 1
        namespace: dict = {}
        try:
            exec(source, namespace)
        except Exception as e:
            raise gopher.ToolError(f"Traceback (most recent call last):\n{e!r}") from e
        if returns is None:
            return None
        # FL evaluates `returns` in the script's namespace; here it is our own fixed text.
        return json.loads(json.dumps(eval(returns, namespace)))

    monkeypatch.setattr(gopher, "page_url", lambda *a: "ws://fake" if state["reachable"] else None)
    monkeypatch.setattr(gopher, "run_piano_roll_python", run)
    return state


def test_requests_are_applied_and_the_state_exported(fl_runner, settings_dir):
    ids = piano_roll._write_request({"action": "add_notes", "notes": [
        {"midi": 60, "time": 0, "duration": 1}, {"midi": 64, "time": 1, "duration": 0.5}]})

    response, message = piano_roll._trigger(ids)

    assert response["status"] == "success"
    assert response["request_ids"] == ids
    assert (response["notes_added"], response["notes_deleted"]) == (2, 0)
    assert [n.number for n in fl_runner["score"].notes] == [60, 64]
    assert "2 added" in message
    assert piano_roll._read_queue() == []
    state = piano_roll._read_state()
    assert [(n["midi"], n["time"]) for n in state["notes"]] == [(60, 0), (64, 1)]


def test_clear_reports_the_notes_deleted(fl_runner):
    for pitch in (60, 62, 64):
        note = FakeNote()
        note.number = pitch
        fl_runner["score"].addNote(note)
    ids = piano_roll._write_request([{"action": "clear"},
                                     {"action": "add_notes", "notes": [
                                         {"midi": 70, "time": 0, "duration": 1}]}])

    response, _ = piano_roll._trigger(ids)

    assert (response["notes_added"], response["notes_deleted"]) == (1, 3)
    assert [n.number for n in fl_runner["score"].notes] == [70]


def test_a_bad_batch_is_an_error_and_dropped(fl_runner):
    ids = piano_roll._write_request({"action": "add_notes", "notes": [{"midi": 60}]})

    response, message = piano_roll._trigger(ids)

    assert response["status"] == "error"
    assert "missing 'duration'" in response["message"]
    assert "error" in message
    assert fl_runner["score"].notes == []
    assert piano_roll._read_queue() == []


def test_unreachable_runner_falls_back_to_the_menu(fl_runner, monkeypatch):
    fl_runner["reachable"] = False
    menu_runs = []
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: menu_runs.append(1) or False)
    ids = piano_roll._write_request({"action": "read"})

    piano_roll._trigger(ids)

    assert fl_runner["runs"] == 0
    assert menu_runs == [1]


def test_a_lost_connection_after_applying_is_not_retried_by_the_menu(fl_runner, monkeypatch):
    fl_runner["fail_after"] = 2  # the count and the edit go through, the read-back doesn't
    menu_runs = []
    monkeypatch.setattr(piano_roll, "run_script_from_menu", lambda: menu_runs.append(1) or False)
    ids = piano_roll._write_request({"action": "add_notes", "notes": [
        {"midi": 60, "time": 0, "duration": 1}]})

    response, message = piano_roll._trigger(ids)

    assert response is None
    assert "applied" in message
    assert menu_runs == []
    assert [n.number for n in fl_runner["score"].notes] == [60]
    assert piano_roll._read_queue() == []
