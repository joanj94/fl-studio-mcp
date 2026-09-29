"""Note transformations and the operation pipeline."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.model import Note
from fl_studio_mcp.music.transform import (
    apply_operations,
    arpeggiate,
    humanize,
    legato,
    quantize,
    repeat,
    reverse,
    set_length,
    shift,
    snap_to_scale,
    stretch,
    transpose,
    velocity_ramp,
)

MELODY = [Note(60, 0, 1), Note(62, 1, 1)]
C_MAJOR = [Note(60, 0, 1), Note(64, 0, 1), Note(67, 0, 1)]


def test_transpose_and_range_check():
    assert [n.pitch for n in transpose(MELODY, 12)] == [72, 74]
    with pytest.raises(ValueError):
        transpose(MELODY, 100)


def test_shift():
    assert [n.start for n in shift(MELODY, 4)] == [4, 5]
    with pytest.raises(ValueError):
        shift(MELODY, -1)


def test_stretch():
    stretched = stretch(MELODY, 2)
    assert [(n.start, n.length) for n in stretched] == [(0, 2), (2, 2)]


def test_reverse_mirrors_within_span():
    assert [(n.pitch, n.start) for n in reverse(MELODY)] == [(62, 0), (60, 1)]


def test_quantize_with_strength():
    notes = [Note(60, 0.13, 0.25)]
    assert quantize(notes, 0.25)[0].start == 0.25
    assert quantize(notes, 0.25, strength=0.5)[0].start == pytest.approx(0.19)


def test_humanize_is_deterministic_and_bounded():
    notes = [Note(60, 1.0, 0.5, velocity=0.8)] * 20

    first = humanize(notes, timing=0.02, velocity=0.1, seed=7)
    second = humanize(notes, timing=0.02, velocity=0.1, seed=7)

    assert first == second
    assert all(abs(n.start - 1.0) <= 0.02 for n in first)
    assert all(0.7 <= n.velocity <= 0.9 for n in first)
    assert len({n.start for n in first}) > 1


def test_humanize_clamps_at_zero():
    assert humanize([Note(60, 0, 1)], timing=0.5, seed=1)[0].start >= 0


@pytest.mark.parametrize(("pitch", "snapped"), [(61, 60), (63, 62), (66, 65), (64, 64)])
def test_snap_to_scale_prefers_lower_on_ties(pitch, snapped):
    assert snap_to_scale([Note(pitch, 0, 1)], "C", "major")[0].pitch == snapped


def test_legato_extends_to_next_onset_per_group():
    notes = [Note(60, 0, 0.25), Note(64, 0, 0.25), Note(62, 1, 0.25)]

    result = legato(notes)

    assert [n.length for n in result] == [1, 1, 0.25]


@pytest.mark.parametrize(
    ("mode", "pitches"),
    [("up", [60, 64, 67, 60]), ("down", [67, 64, 60, 67]), ("updown", [60, 64, 67, 64])],
)
def test_arpeggiate_modes(mode, pitches):
    result = arpeggiate(C_MAJOR, step=0.25, mode=mode)

    assert [n.pitch for n in result] == pitches
    assert [n.start for n in result] == [0, 0.25, 0.5, 0.75]


def test_arpeggiate_octaves_and_gate():
    result = arpeggiate(C_MAJOR, step=0.25, octaves=2, gate=0.5)

    assert [n.pitch for n in result] == [60, 64, 67, 72]
    assert all(n.length == 0.125 for n in result)


def test_arpeggiate_random_is_seeded():
    first = arpeggiate(C_MAJOR, 0.25, "random", seed=3)
    assert first == arpeggiate(C_MAJOR, 0.25, "random", seed=3)


def test_simultaneous_notes_group_despite_float_error():
    chord_notes = [Note(60, 0.1 + 0.2, 1), Note(64, 0.3, 1)]  # 0.30000000000000004 vs 0.3

    arpeggio = arpeggiate(chord_notes, step=0.5)  # one 2-step arpeggio, not two chords

    assert [n.pitch for n in arpeggio] == [60, 64]
    assert [n.start for n in arpeggio] == [pytest.approx(0.3), pytest.approx(0.8)]
    assert [n.length for n in legato([*chord_notes, Note(67, 1.3, 1)])] == [
        pytest.approx(1.0), pytest.approx(1.0), 1,
    ]


def test_growth_is_bounded():
    with pytest.raises(ValueError, match="notes"):
        repeat(MELODY, times=10**9, every=4)
    with pytest.raises(ValueError, match="notes"):
        arpeggiate([Note(60, 0, 10_000)], step=0.001)


def test_repeat():
    assert [n.start for n in repeat(MELODY, times=3, every=4)] == [0, 1, 4, 5, 8, 9]


def test_velocity_ramp_and_set_length():
    ramped = velocity_ramp(MELODY + [Note(64, 2, 1)], 0.2, 1.0)
    assert [n.velocity for n in ramped] == [0.2, pytest.approx(0.6), 1.0]
    assert [n.length for n in set_length(MELODY, 0.5)] == [0.5, 0.5]


def test_apply_operations_pipeline_does_not_mutate_input():
    notes = list(MELODY)

    result = apply_operations(
        notes, [{"op": "transpose", "semitones": 12}, {"op": "shift", "beats": 2}]
    )

    assert [(n.pitch, n.start) for n in result] == [(72, 2), (74, 3)]
    assert notes == MELODY


def test_apply_operations_includes_swing():
    notes = [Note(60, 0, 0.25), Note(60, 0.5, 0.25)]

    result = apply_operations(notes, [{"op": "swing", "amount": 0.5}])

    assert result[1].start == pytest.approx(0.5 + 0.5 * 0.5 / 3)


def test_apply_operations_reports_unknown_op_and_bad_params():
    with pytest.raises(ValueError, match="transpose"):
        apply_operations(MELODY, [{"op": "explode"}])
    with pytest.raises(ValueError, match="shift"):
        apply_operations(MELODY, [{"op": "shift", "bogus": 1}])
    with pytest.raises(ValueError, match="op"):
        apply_operations(MELODY, [{"semitones": 1}])
