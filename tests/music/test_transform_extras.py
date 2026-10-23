"""Note transformations ported from piano roll scripts: overlaps, strum, flam, chop, accents."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.model import Note
from fl_studio_mcp.music.transform import (
    OPERATIONS,
    accent,
    apply_operations,
    chop,
    fix_overlaps,
    flam,
    invert,
    strum,
    velocity_curve,
)

C_MAJOR = [Note(67, 0, 1), Note(60, 0, 1), Note(64, 0, 1)]


def test_fix_overlaps_trims_into_next_note_of_same_pitch():
    notes = [Note(60, 0, 2), Note(60, 1, 1), Note(62, 0.5, 4)]
    fixed = fix_overlaps(notes)
    assert [(n.pitch, n.start, n.length) for n in fixed] == [(60, 0, 1), (62, 0.5, 4), (60, 1, 1)]


def test_fix_overlaps_keeps_one_of_stacked_duplicates():
    notes = [Note(60, 0, 1, velocity=0.5), Note(60, 0, 2, velocity=0.9), Note(60, 0, 1)]
    fixed = fix_overlaps(notes)
    assert len(fixed) == 1
    assert (fixed[0].length, fixed[0].velocity) == (2, 0.9)


def test_strum_delays_each_voice_and_keeps_the_end():
    strummed = strum(C_MAJOR, spread=0.05)
    assert [(n.pitch, n.start) for n in strummed] == [(60, 0), (64, 0.05), (67, 0.1)]
    assert all(abs(n.end - 1) < 1e-9 for n in strummed)


def test_strum_down_and_alternate():
    assert [n.pitch for n in strum(C_MAJOR, 0.05, "down")] == [67, 64, 60]
    two_chords = C_MAJOR + [Note(n.pitch, 1, 1) for n in C_MAJOR]
    alternate = strum(two_chords, 0.05, "alternate")
    assert [n.pitch for n in alternate] == [60, 64, 67, 67, 64, 60]


def test_strum_rejects_a_spread_longer_than_the_chord():
    with pytest.raises(ValueError):
        strum(C_MAJOR, spread=0.6)


def test_flam_adds_a_quieter_grace_note_before():
    flammed = flam([Note(38, 1, 0.25, velocity=0.8)], offset=0.05, velocity=0.5)
    assert [(n.start, n.velocity) for n in flammed] == [(0.95, 0.4), (1, 0.8)]
    assert flammed[0].length == pytest.approx(0.05)


def test_flam_skips_notes_too_close_to_the_pattern_start():
    assert len(flam([Note(38, 0, 0.25)], offset=0.05)) == 1


def test_chop_by_size_and_by_parts():
    assert [(n.start, n.length) for n in chop([Note(60, 0, 1)], size=0.25)] == [
        (0, 0.25), (0.25, 0.25), (0.5, 0.25), (0.75, 0.25)
    ]
    chopped = chop([Note(60, 2, 1)], parts=2, gate=0.5)
    assert [(n.start, n.length) for n in chopped] == [(2, 0.25), (2.5, 0.25)]
    with pytest.raises(ValueError):
        chop([Note(60, 0, 1)])
    with pytest.raises(ValueError):
        chop([Note(60, 0, 1)], size=0.25, parts=2)


def test_chop_keeps_a_short_last_piece():
    chopped = chop([Note(60, 0, 1)], size=0.4)
    assert [n.length for n in chopped] == pytest.approx([0.4, 0.4, 0.2])


@pytest.mark.parametrize("shape", ["linear", "exp", "log", "sine"])
def test_velocity_curve_runs_from_start_to_end(shape):
    notes = [Note(60, i, 1) for i in range(5)]
    curved = velocity_curve(notes, 0.2, 1.0, shape)
    assert curved[0].velocity == pytest.approx(0.2)
    assert curved[-1].velocity == pytest.approx(1.0)
    assert all(0.2 - 1e-9 <= n.velocity <= 1 + 1e-9 for n in curved)


def test_velocity_curve_shapes_differ_in_the_middle():
    notes = [Note(60, i, 1) for i in range(3)]
    middle = {s: velocity_curve(notes, 0, 1, s)[1].velocity for s in ("linear", "exp", "log")}
    assert middle["exp"] < middle["linear"] < middle["log"]
    with pytest.raises(ValueError):
        velocity_curve(notes, 0, 1, "zigzag")


def test_accent_scales_velocity_by_grid_position():
    notes = [Note(60, i * 0.25, 0.25, velocity=0.8) for i in range(4)]
    accented = accent(notes, [1, 0.5], every=0.25)
    assert [n.velocity for n in accented] == pytest.approx([0.8, 0.4, 0.8, 0.4])
    with pytest.raises(ValueError):
        accent(notes, [], every=0.25)


def test_accent_clamps_to_full_velocity():
    assert accent([Note(60, 0, 1, velocity=0.8)], [2], every=1)[0].velocity == 1


def test_invert_mirrors_pitches_around_an_axis():
    assert [n.pitch for n in invert(C_MAJOR, "C4")] == [53, 60, 56]


def test_new_operations_are_registered_and_chain():
    for name in ("fix_overlaps", "strum", "flam", "chop", "velocity_curve", "accent", "invert"):
        assert name in OPERATIONS
    result = apply_operations(C_MAJOR, [{"op": "strum", "spread": 0.02}, {"op": "flam"}])
    # Voices start at 0, 0.02, 0.04; only the last lies far enough in for a 0.03 grace note.
    assert len(result) == 4


def test_step_hits_of_length_zero_can_be_strummed_and_chopped():
    hits = [Note(36, 0, 0.0001), Note(42, 0, 0.0001)]
    assert len(strum(hits, spread=0.00005)) == 2
    assert [n.length for n in chop([Note(60, 0, 1)], parts=3)] == pytest.approx([1 / 3] * 3)
