"""Notes, pitch names and musical time."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.model import (
    MAX_NOTES,
    Note,
    TimeSignature,
    bars_to_beats,
    check_note_count,
    parse_pitch,
    pitch_name,
    position,
)


@pytest.mark.parametrize(
    ("name", "midi"),
    [("C4", 60), ("c4", 60), ("F#3", 54), ("Bb2", 46), ("C-1", 0), ("G9", 127),
     ("Cb4", 59), ("E#4", 65), (64, 64)],
)
def test_parse_pitch(name, midi):
    assert parse_pitch(name) == midi


@pytest.mark.parametrize("bad", ["H2", "C", "G#9", 128, -1, "C4x", True])
def test_parse_pitch_rejects_invalid(bad):
    with pytest.raises(ValueError):
        parse_pitch(bad)


def test_pitch_name():
    assert pitch_name(60) == "C4"
    assert pitch_name(61) == "C#4"
    assert pitch_name(61, prefer_flats=True) == "Db4"
    assert pitch_name(0) == "C-1"


def test_note_validates_ranges():
    with pytest.raises(ValueError):
        Note(pitch=128, start=0, length=1)
    with pytest.raises(ValueError):
        Note(pitch=60, start=-0.5, length=1)
    with pytest.raises(ValueError):
        Note(pitch=60, start=0, length=0)
    with pytest.raises(ValueError):
        Note(pitch=60, start=0, length=1, velocity=1.5)
    with pytest.raises(ValueError):
        Note(pitch=60, start=0, length=1, pan=-0.1)


def test_note_is_immutable():
    note = Note(pitch=60, start=0, length=1)
    with pytest.raises(AttributeError):
        note.pitch = 61  # type: ignore[misc]


def test_to_dict_uses_fl_send_notes_format_and_omits_defaults():
    assert Note(60, 1.5, 0.5).to_dict() == {
        "midi": 60, "time": 1.5, "duration": 0.5, "velocity": 0.8,
    }
    assert Note(60, 0, 1, pan=0.2, slide=True).to_dict()["slide"] is True


def test_from_dict_accepts_aliases_and_names():
    note = Note.from_dict({"pitch": "A4", "start": 2, "length": 0.5, "velocity": 1.0})
    assert note == Note(69, 2, 0.5, velocity=1.0)
    assert Note.from_dict({"midi": 60, "duration": 1}) == Note(60, 0, 1)


def test_from_dict_requires_pitch_and_length():
    with pytest.raises(ValueError, match="pitch"):
        Note.from_dict({"duration": 1})
    with pytest.raises(ValueError, match="duration"):
        Note.from_dict({"midi": 60})


@pytest.mark.parametrize(
    "bad",
    [
        {"midi": 60, "duration": 1, "time": None},
        {"midi": 60, "duration": 1, "velocity": None},
        {"midi": 60, "duration": [1]},
        {"midi": 60, "duration": 1, "pan": "left"},
        {"midi": 60, "duration": True},
    ],
)
def test_from_dict_rejects_non_numeric_fields_with_value_error(bad):
    with pytest.raises(ValueError):
        Note.from_dict(bad)


def test_check_note_count():
    check_note_count(MAX_NOTES)
    with pytest.raises(ValueError, match=str(MAX_NOTES)):
        check_note_count(MAX_NOTES + 1)


def test_round_trip():
    note = Note(62, 3.25, 0.75, velocity=0.6, pan=0.3, porta=True)
    assert Note.from_dict(note.to_dict()) == note


def test_time_signature_and_positions():
    assert TimeSignature(6, 8).beats_per_bar == 3.0
    assert bars_to_beats(2) == 8.0
    assert bars_to_beats(2, TimeSignature(3, 4)) == 6.0
    assert position(bar=2, beat=3) == 6.0
    assert position(bar=1) == 0.0
    with pytest.raises(ValueError):
        position(bar=0)
