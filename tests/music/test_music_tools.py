"""MCP music tools: pure note generation, no FL Studio involved."""

from __future__ import annotations

import pytest

from fl_studio_mcp.tools.music import register_music_tools
from tests.fakes import ToolCollector


@pytest.fixture
def tools() -> dict:
    collector = ToolCollector()
    register_music_tools(collector)
    return collector.tools


def test_reference_lists_vocabulary(tools):
    reference = tools["music_reference"]()

    assert "dorian" in reference["scales"]
    assert "m7" in reference["chord_qualities"]
    assert "semitones" in reference["transform_operations"]["transpose"]
    assert "C4 = MIDI 60" in reference["pitch_names"]


def test_scale(tools):
    result = tools["music_scale"]("A", "minor", octave=3)

    assert result["pitches"][0] == 57
    assert result["names"][:3] == ["A3", "B3", "C4"]


def test_chords_from_numerals_are_laid_out_in_time(tools):
    result = tools["music_chords"](["i", "VI"], key="F", scale="minor", octave=3,
                                   beats_per_chord=2, start=4)

    assert [c["symbol"] for c in result["chords"]] == ["Fm", "Db"]
    assert [c["start"] for c in result["chords"]] == [4, 6]
    assert result["count"] == 6
    assert {n["time"] for n in result["notes"]} == {4, 6}
    assert all(n["duration"] == 2 for n in result["notes"])


def test_chords_inversion(tools):
    result = tools["music_chords"](["C"], inversion=1)

    assert result["chords"][0]["pitches"] == [64, 67, 72]


def test_chords_error_is_returned_not_raised(tools):
    result = tools["music_chords"](["V"])

    assert "needs a key" in result["error"]


def test_degrees_with_rests_and_lengths(tools):
    result = tools["music_degrees"]([1, None, 5, 8], key="C", scale="major",
                                    step=0.5, lengths=[0.5, 0.5, 1, 1])

    assert [(n["midi"], n["time"], n["duration"]) for n in result["notes"]] == [
        (60, 0, 0.5), (67, 1.0, 1), (72, 1.5, 1),
    ]


def test_degrees_lengths_must_match(tools):
    assert "error" in tools["music_degrees"]([1, 2], key="C", lengths=[1])


def test_rhythm(tools):
    result = tools["music_rhythm"]("x..x..x.", ["C2"], step=0.25, repeat=2)

    assert result["count"] == 6
    assert result["end"] == pytest.approx(3.75)
    assert set(result["notes"][0]) == {"midi", "time", "duration", "velocity"}


def test_rhythm_error(tools):
    assert "Unknown grid symbol" in tools["music_rhythm"]("x.o.", [36])["error"]


def test_roll(tools):
    result = tools["music_roll"](start=0, length=2, rates=[2, 4], pitch="D2")

    assert result["count"] == 6


def test_transform_pipeline(tools):
    notes = tools["music_chords"](["C"], beats_per_chord=1)["notes"]

    result = tools["music_transform"](
        notes, [{"op": "arpeggiate", "step": 0.25}, {"op": "transpose", "semitones": -12}]
    )

    assert [n["midi"] for n in result["notes"]] == [48, 52, 55, 48]


def test_transform_rejects_bad_notes(tools):
    assert "error" in tools["music_transform"]([{"time": 0}], [])
    assert "error" in tools["music_transform"]([{"midi": 60, "duration": 1, "time": None}], [])


def test_oversized_requests_return_errors(tools):
    assert "error" in tools["music_scale"]("C", octaves=100_000_000)
    assert "error" in tools["music_chords"](["C"], inversion=10**9)
    assert "error" in tools["music_roll"](0, 4, [100_000_000], 60)
