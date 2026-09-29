"""Scales, chords and roman numeral progressions."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.theory import (
    chord,
    degree_pitch,
    invert,
    progression,
    roman_to_symbol,
    scale_pitches,
)


def test_scale_pitches_includes_top_root():
    assert scale_pitches("C", "major", octave=4) == [60, 62, 64, 65, 67, 69, 71, 72]


def test_scale_pitches_multiple_octaves():
    pitches = scale_pitches("F", "minor", octave=3, octaves=2)
    assert pitches[0] == 53 and pitches[-1] == 77 and len(pitches) == 15


def test_unknown_scale_lists_known_ones():
    with pytest.raises(ValueError, match="dorian"):
        scale_pitches("C", "nope")


@pytest.mark.parametrize(("degree", "midi"), [(1, 60), (3, 64), (8, 72), (0, 59), (-1, 57)])
def test_degree_pitch_wraps_octaves(degree, midi):
    assert degree_pitch("C", "major", degree, octave=4) == midi


@pytest.mark.parametrize(
    ("symbol", "octave", "pitches"),
    [
        ("C", 4, [60, 64, 67]),
        ("Fm7", 3, [53, 56, 60, 63]),
        ("C/E", 4, [52, 60, 64, 67]),
        ("Bbmaj7", 3, [58, 62, 65, 69]),
        ("F#dim", 4, [66, 69, 72]),
        ("Asus4", 3, [57, 62, 64]),
        ("E5", 2, [40, 47]),
    ],
)
def test_chord_symbols(symbol, octave, pitches):
    assert chord(symbol, octave) == pitches


def test_unknown_chord_quality():
    with pytest.raises(ValueError, match="maj7"):
        chord("Cwhatever")


def test_scale_octaves_are_bounded_before_building():
    with pytest.raises(ValueError, match="octaves"):
        scale_pitches("C", "major", octaves=100_000_000)


def test_invert_rejects_empty_and_absurd_inversions():
    with pytest.raises(ValueError):
        invert([], 1)
    with pytest.raises(ValueError, match="MIDI range"):
        invert([60, 64, 67], 10**9)


def test_invert():
    assert invert([60, 64, 67], 1) == [64, 67, 72]
    assert invert([60, 64, 67], 2) == [67, 72, 76]
    assert invert([60, 64, 67], -1) == [55, 60, 64]
    assert invert([60, 64, 67], 0) == [60, 64, 67]


@pytest.mark.parametrize(
    ("numeral", "key", "scale", "symbol"),
    [
        ("i", "F", "minor", "Fm"),
        ("VI", "F", "minor", "Db"),
        ("III", "F", "minor", "Ab"),
        ("VII", "F", "minor", "Eb"),
        ("V7", "C", "major", "G7"),
        ("ii", "C", "major", "Dm"),
        ("vii°", "C", "major", "Bdim"),
        ("bVII", "C", "major", "Bb"),
        ("V", "A", "harmonic_minor", "E"),
        ("Imaj7", "D", "major", "Dmaj7"),
        ("ii7", "C", "major", "Dm7"),
        ("viiø7", "C", "major", "Bm7b5"),
        ("#iv", "C", "major", "F#m"),
    ],
)
def test_roman_to_symbol(numeral, key, scale, symbol):
    assert roman_to_symbol(numeral, key, scale) == symbol


def test_roman_rejects_garbage():
    with pytest.raises(ValueError):
        roman_to_symbol("X", "C", "major")


def test_progression_from_dash_string():
    chords = progression("i-VI-III-VII", key="F", scale="minor", octave=3)

    assert [c.symbol for c in chords] == ["Fm", "Db", "Ab", "Eb"]
    assert chords[0].pitches == (53, 56, 60)


def test_progression_accepts_symbols_and_numerals():
    chords = progression(["Am", "IV", "C/G"], key="C", scale="major")

    assert [c.symbol for c in chords] == ["Am", "F", "C/G"]
