"""Smooth voice leading between chords, and basslines from chord roots."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.theory import bassline, chord, chord_root, voice_lead
from fl_studio_mcp.tools import music
from tests.fakes import ToolCollector


def movement(voicings: list[list[int]]) -> int:
    return sum(sum(abs(a - b) for a, b in zip(x, y)) for x, y in zip(voicings, voicings[1:]))


def test_voice_leading_keeps_pitch_classes_and_moves_less():
    root_position = [chord("C"), chord("F"), chord("G"), chord("C")]

    smooth = voice_lead(root_position)

    assert smooth[0] == root_position[0]
    for led, original in zip(smooth, root_position):
        assert sorted(p % 12 for p in led) == sorted(p % 12 for p in original)
    assert movement(smooth) < movement(root_position)
    assert smooth[1] == [60, 65, 69]  # C F A: the C stays, two voices step up


def test_voice_leading_stays_near_the_first_chords_register():
    smooth = voice_lead([chord("C")] + [chord(s) for s in ["G", "Am", "F"] * 4])
    assert all(48 <= p <= 84 for v in smooth for p in v)


def test_chord_roots_ignore_slash_basses():
    assert chord_root("C/E") == 0
    assert chord_root("F#m7") == 6


@pytest.mark.parametrize("movement_name, pitches", [
    ("root", [41, 41, 41, 41]),
    ("root_fifth", [41, 48, 41, 48]),
    ("octave", [41, 53, 41, 53]),
])
def test_bassline_movements(movement_name, pitches):
    notes = bassline(["Fm"], grid="x...x...x...x...", step=0.25, beats_per_chord=4,
                     octave=2, movement=movement_name)

    assert [n.pitch for n in notes] == pitches
    assert [n.start for n in notes] == [0, 1, 2, 3]
    assert all(n.length == pytest.approx(0.25) for n in notes)


def test_bassline_repeats_the_grid_over_each_chord():
    notes = bassline(["C", "G"], grid="x.x.", step=0.5, beats_per_chord=4, octave=2)

    assert [(n.pitch, n.start) for n in notes] == [
        (36, 0), (36, 1), (36, 2), (36, 3), (43, 4), (43, 5), (43, 6), (43, 7)]


def test_bassline_refuses_a_bad_movement_or_grid():
    with pytest.raises(ValueError):
        bassline(["C"], grid="x...", movement="jump")
    with pytest.raises(ValueError):
        bassline(["C"], grid="....")


def test_music_tools_offer_smooth_voicing_and_basslines():
    collector = ToolCollector()
    music.register_music_tools(collector)

    led = collector.tools["music_chords"](["C", "F"], voicing="smooth")
    assert led["chords"][1]["pitches"] == [60, 65, 69]
    bass = collector.tools["music_bassline"](["i", "VI"], key="A", scale="minor", grid="x.x.",
                                             step=1, beats_per_chord=4)
    assert [n["midi"] for n in bass["notes"]] == [45, 45, 41, 41]
