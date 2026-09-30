"""Tests for the pitch measurement (which note a sound plays, and how far off it is)."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio import pitch

RATE = 44100


def tone(hz: float, seconds: float = 1.0, harmonics: tuple[float, ...] = (1.0,)) -> np.ndarray:
    """A steady tone; `harmonics` are the levels of the fundamental and its overtones."""
    t = np.arange(int(seconds * RATE)) / RATE
    return sum(level * np.sin(2 * np.pi * hz * (n + 1) * t) for n, level in enumerate(harmonics))


def hz_of(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


@pytest.mark.parametrize("midi, name", [(29, "F1"), (45, "A2"), (60, "C4"), (84, "C6")])
def test_a_steady_tone_is_named_by_its_note(midi, name):
    found = pitch.estimate_pitch(tone(hz_of(midi)), RATE)

    assert (found["note"], found["midi"]) == (name, midi)
    assert abs(found["cents"]) <= 3
    assert found["hz"] == pytest.approx(hz_of(midi), rel=0.003)
    assert found["share"] == 1.0


def test_a_detuned_tone_reports_how_many_cents_it_is_off():
    found = pitch.estimate_pitch(tone(hz_of(57.3)), RATE)

    assert found["note"] == "A3"
    assert found["cents"] == pytest.approx(30, abs=3)


def test_the_fundamental_is_found_under_louder_overtones():
    found = pitch.estimate_pitch(tone(hz_of(36), harmonics=(0.3, 1.0, 0.8, 0.5)), RATE)

    assert found["note"] == "C2"


def test_a_missing_fundamental_is_still_heard_as_the_note():
    found = pitch.estimate_pitch(tone(hz_of(48), harmonics=(0.0, 1.0, 1.0, 1.0)), RATE)

    assert found["note"] == "C3"


def test_the_note_held_longest_wins_and_the_others_are_listed():
    melody = np.concatenate([tone(hz_of(60), 1.5), tone(hz_of(67), 0.5)])

    found = pitch.estimate_pitch(melody, RATE)

    assert found["note"] == "C4"
    assert [entry["note"] for entry in found["notes"]][:2] == ["C4", "G4"]
    assert found["notes"][0]["share"] == pytest.approx(0.75, abs=0.06)


def test_a_drum_like_sweep_is_named_by_the_pitch_it_settles_on():
    t = np.arange(int(0.6 * RATE)) / RATE
    hz = hz_of(29) + 300 * np.exp(-t / 0.015)  # drops to F1 within a few hundredths
    kick = np.sin(2 * np.pi * np.cumsum(hz) / RATE) * np.exp(-t / 0.25)

    found = pitch.estimate_pitch(kick, RATE)

    assert found["note"] == "F1"


def test_noise_and_silence_have_no_pitch():
    noise = np.random.default_rng(1).uniform(-1, 1, RATE)

    assert pitch.estimate_pitch(noise, RATE) is None
    assert pitch.estimate_pitch(np.zeros(RATE), RATE) is None
    assert pitch.estimate_pitch(np.zeros(10), RATE) is None


def test_silence_around_the_sound_does_not_count():
    padded = np.concatenate([np.zeros(RATE), tone(hz_of(50), 0.5), np.zeros(RATE)])

    found = pitch.estimate_pitch(padded, RATE)

    assert found["note"] == "D3"
    assert found["voiced"] >= 0.9  # only the frames half in the silence are unclear


def test_other_sample_rates_give_the_same_note():
    t = np.arange(48000) / 48000

    assert pitch.estimate_pitch(np.sin(2 * np.pi * hz_of(41) * t), 48000)["note"] == "F2"


def test_semitones_between_is_the_shortest_way_from_one_note_to_a_pitch_class():
    assert pitch.semitones_to_pitch_class(31, 5) == -2   # G1 down to F
    assert pitch.semitones_to_pitch_class(29, 5) == 0
    assert pitch.semitones_to_pitch_class(24, 5) == 5    # C up to F
    assert pitch.semitones_to_pitch_class(35, 5) == -6   # a tritone goes down


def test_octaves_of_one_note_share_a_pitch_class():
    octaves = np.concatenate([tone(hz_of(60), 0.5), tone(hz_of(72), 0.5)])

    found = pitch.estimate_pitch(octaves, RATE)

    assert found["share"] < 0.6
    assert found["class_share"] >= 0.95


def test_the_note_a_glide_ends_on_is_told():
    glide = np.concatenate([tone(hz_of(64), 0.6), tone(hz_of(62), 0.6), tone(hz_of(60), 0.5)])

    found = pitch.estimate_pitch(glide, RATE)

    assert found["ends_on"] == "C4"
