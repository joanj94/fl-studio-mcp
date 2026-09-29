"""FL-side ComposeWithLLM piano roll script: request processing and response."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

EXISTING_PITCH = 50


def _add_existing_note(flp) -> None:
    note = flp.Note()
    note.number = EXISTING_PITCH
    flp.score.addNote(note)


def _queue(script, requests: list[dict]) -> None:
    Path(script.REQUEST_FILE).write_text(json.dumps(requests))


def _response(script) -> dict:
    return json.loads(Path(script.RESPONSE_FILE).read_text())


def test_add_notes_converts_quarter_notes_to_ticks(piano_roll_script, flp):
    _queue(
        piano_roll_script,
        [{"id": "a", "action": "add_notes",
          "notes": [{"midi": 64, "time": 1.5, "duration": 0.25, "velocity": 0.5}]}],
    )

    piano_roll_script.apply()

    (note,) = flp.score.notes
    assert (note.number, note.time, note.length, note.velocity) == (64, 144, 24, 0.5)


def test_add_chord_applies_base_time_and_offsets(piano_roll_script, flp):
    _queue(
        piano_roll_script,
        [{"id": "c", "action": "add_chord", "time": 2, "duration": 1.0,
          "notes": [{"midi": 60}, {"midi": 64, "offset": 0.5}]}],
    )

    piano_roll_script.apply()

    assert [(n.number, n.time, n.length) for n in flp.score.notes] == [
        (60, 192, 96),
        (64, 240, 96),
    ]


def test_clear_then_delete_and_add_run_in_order(piano_roll_script, flp):
    for number in (60, 62):
        note = flp.Note()
        note.number, note.time, note.length = number, 0, 96
        flp.score.addNote(note)
    _queue(
        piano_roll_script,
        [
            {"id": "1", "action": "delete_notes", "notes": [{"midi": 60, "time": 0}]},
            {"id": "2", "action": "add_notes", "notes": [{"midi": 67, "duration": 1}]},
        ],
    )

    piano_roll_script.apply()

    assert sorted(n.number for n in flp.score.notes) == [62, 67]
    response = _response(piano_roll_script)
    assert (response["notes_added"], response["notes_deleted"]) == (1, 1)


def test_clear_removes_all_notes(piano_roll_script, flp):
    flp.score.addNote(flp.Note())
    _queue(piano_roll_script, [{"id": "z", "action": "clear"}])

    piano_roll_script.apply()

    assert flp.score.notes == []


def test_response_lists_processed_request_ids(piano_roll_script):
    _queue(
        piano_roll_script,
        [{"id": "r1", "action": "clear"}, {"id": "r2", "action": "add_notes", "notes": []}],
    )

    piano_roll_script.apply()

    response = _response(piano_roll_script)
    assert response["status"] == "success"
    assert response["request_ids"] == ["r1", "r2"]


def test_queue_is_emptied_after_processing(piano_roll_script):
    _queue(piano_roll_script, [{"id": "r1", "action": "clear"}])

    piano_roll_script.apply()

    assert json.loads(Path(piano_roll_script.REQUEST_FILE).read_text()) == []


def test_failure_response_still_lists_request_ids(piano_roll_script, flp, monkeypatch):
    flp.score.addNote(flp.Note())

    def broken_delete(index):
        raise RuntimeError("score locked")

    monkeypatch.setattr(flp.score, "deleteNote", broken_delete)
    _queue(piano_roll_script, [{"id": "c1", "action": "clear"}])

    piano_roll_script.apply()

    response = _response(piano_roll_script)
    assert response["status"] == "error"
    assert "score locked" in response["message"]
    assert response["request_ids"] == ["c1"]
    # The failed batch is dropped so it isn't replayed on the next trigger.
    assert json.loads(Path(piano_roll_script.REQUEST_FILE).read_text()) == []


def test_files_are_written_without_renaming(piano_roll_script, monkeypatch):
    # FL Studio 2026's os.replace returns NULL without an exception and leaves the
    # embedded interpreter broken for every later call (seen live), so never call it.
    calls = []
    monkeypatch.setattr(piano_roll_script.os, "replace", lambda *args: calls.append(args))
    monkeypatch.setattr(piano_roll_script.os, "remove", lambda *args: calls.append(args))
    monkeypatch.setattr(piano_roll_script.os, "unlink", lambda *args: calls.append(args))
    _queue(piano_roll_script, [{"id": "r1", "action": "clear"}])

    piano_roll_script.apply()

    assert _response(piano_roll_script)["request_ids"] == ["r1"]
    assert json.loads(Path(piano_roll_script.REQUEST_FILE).read_text()) == []
    assert calls == []


def test_add_notes_sets_optional_note_properties(piano_roll_script, flp):
    _queue(piano_roll_script, [{"id": "p", "action": "add_notes", "notes": [
        {"midi": 60, "duration": 1, "pan": 0.25, "release": 0.3, "slide": True, "porta": True,
         "pitchofs": -12, "muted": True, "color": 3, "fcut": 0.1, "fres": 0.9},
    ]}])

    piano_roll_script.apply()

    (note,) = flp.score.notes
    assert (note.pan, note.release, note.slide, note.porta, note.pitchofs, note.muted,
            note.color, note.fcut, note.fres) == (0.25, 0.3, True, True, -12, True, 3, 0.1, 0.9)


def test_failed_sub_request_fails_the_batch(piano_roll_script, flp):
    _queue(piano_roll_script, [
        {"id": "ok", "action": "add_notes", "notes": [{"midi": 60, "duration": 1}]},
        {"id": "bad", "action": "add_notes", "notes": [{"midi": 62}]},  # no duration
    ])

    piano_roll_script.apply()

    response = _response(piano_roll_script)
    assert response["status"] == "error"
    assert "duration" in response["message"]
    assert response["request_ids"] == ["ok", "bad"]
    assert flp.score.notes == []  # checked before anything is applied


@pytest.mark.parametrize("bad", [
    {"action": "add_chord", "notes": [{"duration": 1}]},  # no midi
    {"action": "delete_notes", "notes": [{"midi": 60}]},  # no time
    {"action": "add_notes", "notes": "C4"},  # not a list
    {"action": "add_notes", "notes": [60]},  # not a dict
])
def test_invalid_request_later_in_batch_changes_nothing(piano_roll_script, flp, bad):
    _add_existing_note(flp)
    _queue(piano_roll_script, [
        {"id": "c", "action": "clear"},
        {"id": "ok", "action": "add_notes", "notes": [{"midi": 60, "duration": 1}]},
        {"id": "bad", **bad},
    ])

    piano_roll_script.apply()

    assert _response(piano_roll_script)["status"] == "error"
    assert [n.number for n in flp.score.notes] == [EXISTING_PITCH]


def test_unknown_action_fails_the_batch(piano_roll_script, flp):
    _add_existing_note(flp)
    _queue(piano_roll_script, [
        {"id": "c", "action": "clear"},
        {"id": "u", "action": "transpose_all"},
    ])

    piano_roll_script.apply()

    response = _response(piano_roll_script)
    assert response["status"] == "error"
    assert "transpose_all" in response["message"]
    assert [n.number for n in flp.score.notes] == [EXISTING_PITCH]


def test_read_request_only_exports_state(piano_roll_script, flp):
    flp.score.addNote(flp.Note())
    _queue(piano_roll_script, [{"id": "r", "action": "read"}])

    piano_roll_script.apply()

    response = _response(piano_roll_script)
    assert response["status"] == "success"
    assert (response["notes_added"], response["notes_deleted"]) == (0, 0)
    assert json.loads(Path(piano_roll_script.STATE_FILE).read_text())["noteCount"] == 1


def test_empty_queue_still_writes_idle_response(piano_roll_script):
    piano_roll_script.apply()

    assert _response(piano_roll_script) == {"status": "idle", "request_ids": []}


def test_state_is_exported_in_quarter_notes(piano_roll_script, flp):
    _queue(piano_roll_script, [{"id": "a", "action": "add_notes",
                                "notes": [{"midi": 60, "time": 1, "duration": 2}]}])

    piano_roll_script.apply()

    state = json.loads(Path(piano_roll_script.STATE_FILE).read_text())
    assert state["noteCount"] == 1
    assert (state["notes"][0]["time"], state["notes"][0]["duration"]) == (1.0, 2.0)


def test_script_dir_uses_resolved_documents_folder(piano_roll_script, tmp_path, monkeypatch):
    monkeypatch.setattr(piano_roll_script, "get_documents_dir", lambda: str(tmp_path / "Docs"))

    expected = tmp_path / "Docs" / "Image-Line" / "FL Studio" / "Settings" / "Piano roll scripts"
    assert Path(piano_roll_script.get_script_dir()) == expected
