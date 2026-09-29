"""Step grids, Euclidean rhythms, rolls and swing."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.model import Note
from fl_studio_mcp.music.rhythm import (
    Hit,
    euclid,
    expand_pattern,
    parse_grid,
    rhythm_notes,
    roll,
    swing,
)


def test_euclid():
    assert euclid(3, 8) == "x..x..x."
    assert euclid(3, 8, rotation=1) == "..x..x.x"
    assert euclid(4, 4) == "xxxx"
    assert euclid(0, 4) == "...."
    with pytest.raises(ValueError):
        euclid(5, 4)


def test_expand_pattern_supports_euclid_syntax():
    assert expand_pattern("E(3,8)") == "x..x..x."
    assert expand_pattern("E(3, 8, 1) x...") == "..x..x.xx..."
    assert expand_pattern("x.x.|x.x.") == "x.x.x.x."


def test_parse_grid_hits_ties_and_accents():
    hits, steps = parse_grid("x.x_|X...")

    assert steps == 8
    assert hits == [Hit(0, 1, False), Hit(2, 2, False), Hit(4, 1, True)]


def test_parse_grid_rejects_unknown_symbols():
    with pytest.raises(ValueError, match="'o'"):
        parse_grid("x.o.")


def test_rhythm_notes_basic():
    notes = rhythm_notes("x...x...", ["C1"], step=0.25)

    assert notes == [Note(24, 0.0, 0.25), Note(24, 1.0, 0.25)]


def test_rhythm_notes_accents_gate_start_and_chords():
    notes = rhythm_notes(
        "X.x.", [60, 64], step=0.5, start=4, velocity=0.5, accent_velocity=1.0, gate=0.5
    )

    assert [(n.pitch, n.start, n.length, n.velocity) for n in notes] == [
        (60, 4.0, 0.25, 1.0), (64, 4.0, 0.25, 1.0),
        (60, 5.0, 0.25, 0.5), (64, 5.0, 0.25, 0.5),
    ]


def test_rhythm_notes_repeat():
    notes = rhythm_notes("x...", [36], step=0.25, repeat=3)

    assert [n.start for n in notes] == [0.0, 1.0, 2.0]


def test_rhythm_notes_tie_extends_length():
    (note,) = rhythm_notes("x___", [48], step=0.25)

    assert note.length == 1.0


def test_roll_accelerates_and_ramps_velocity():
    notes = roll(start=0, length=4, rates=[1, 2, 4, 8], pitch="D2",
                 velocity_start=0.5, velocity_end=1.0)

    assert len(notes) == 15
    assert notes[0].start == 0 and notes[1].start == 1.0
    assert notes[-1].start == pytest.approx(3.875)
    assert notes[-1].length == pytest.approx(0.125)
    assert notes[0].velocity == 0.5 and notes[-1].velocity == 1.0
    assert all(n.pitch == 38 for n in notes)


def test_generation_size_is_bounded():
    with pytest.raises(ValueError, match="notes"):
        roll(start=0, length=4, rates=[100_000_000], pitch=60)
    with pytest.raises(ValueError, match="notes"):
        rhythm_notes("x...", [36], repeat=10**9)


def test_swing_delays_off_beats_only():
    notes = [Note(60, t, 0.25) for t in (0, 0.5, 1.0, 1.5)]

    swung = swing(notes, amount=1.0, grid=0.5)

    assert [round(n.start, 4) for n in swung] == [0, 0.6667, 1.0, 1.6667]
    assert notes[1].start == 0.5  # input untouched
