"""Chords over time from audio (chroma per span matched to triad templates)."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio.chords import detect_chords

RATE = 22050


def triad(pitches: list[int], seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    tone = sum(np.sin(2 * np.pi * 440 * 2 ** ((p - 69) / 12) * t) for p in pitches)
    return 0.2 * tone


def test_a_minor_then_f_major_bar_by_bar():
    # 120 BPM: a bar of 4 beats is 2 s.
    audio = np.concatenate([triad([57, 60, 64], 2), triad([57, 60, 64], 2),
                            triad([53, 57, 60], 2), triad([53, 57, 60], 2)])

    found = detect_chords(audio, RATE, bpm=120, beats_per_chord=4)

    assert [c["chord"] for c in found] == ["Am", "Am", "F", "F"]
    assert [c["time"] for c in found] == [0, 4, 8, 12]
    assert all(c["confidence"] > 0 for c in found)


def test_silence_is_no_chord():
    found = detect_chords(np.zeros(4 * RATE), RATE, bpm=120, beats_per_chord=4)

    assert [c["chord"] for c in found] == ["N", "N"]


def test_start_offset_and_spans_shorter_than_a_bar():
    audio = np.concatenate([np.zeros(RATE), triad([60, 64, 67], 1), triad([62, 65, 69], 1)])

    found = detect_chords(audio, RATE, bpm=120, beats_per_chord=2, start_seconds=1.0)

    assert [c["chord"] for c in found] == ["C", "Dm"]


def test_bad_arguments_are_refused():
    with pytest.raises(ValueError):
        detect_chords(np.zeros(RATE), RATE, bpm=0)
    with pytest.raises(ValueError):
        detect_chords(np.zeros(RATE), RATE, bpm=120, beats_per_chord=0)
