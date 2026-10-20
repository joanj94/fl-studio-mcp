"""Technical checks of a render: DC, true peak, clicks, dropouts, phase."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio.qc import check, true_peak

RATE = 44100


def tone(seconds=2.0, freq=220.0, level=0.5):
    t = np.arange(int(seconds * RATE)) / RATE
    mono = level * np.sin(2 * np.pi * freq * t)
    return np.column_stack([mono, mono])


def test_a_clean_tone_has_no_problems():
    result = check(tone(), RATE)

    assert result["problems"] == []
    assert result["true_peak_dbtp"] == pytest.approx(-6.02, abs=0.1)
    assert result["phase"]["mono_loss_db"] == pytest.approx(0, abs=0.1)


def test_true_peak_finds_the_peak_between_samples():
    # A sine at a quarter of the rate, sampled 45 degrees off its peaks: every
    # sample reads 0.707 of the real peak.
    n = np.arange(4096)
    samples = np.sin(np.pi / 2 * n + np.pi / 4)[:, None]

    assert np.max(np.abs(samples)) == pytest.approx(0.707, abs=0.001)
    assert true_peak(samples) == pytest.approx(1.0, abs=0.02)


def test_dc_offset_click_and_dropout_are_found():
    audio = tone(3.0) + 0.01
    audio[RATE, :] += 0.8  # a click at 1 s
    audio[2 * RATE:2 * RATE + RATE // 10, :] = 0.0  # 100 ms gap at 2 s

    result = check(audio, RATE)

    assert result["dc_offset"][0] == pytest.approx(0.01, abs=0.002)
    assert any(abs(t - 1.0) < 0.002 for t in result["clicks"])
    assert result["dropouts"] == [{"start": 2.0, "seconds": 0.1}]
    assert len(result["problems"]) == 3


def test_out_of_phase_channels_are_reported():
    audio = tone()
    audio[:, 1] *= -1

    result = check(audio, RATE)

    assert result["phase"]["out_of_phase_windows"] == 2
    assert any("mono sum" in p for p in result["problems"])


def test_silence_at_the_ends_is_no_dropout_and_mono_has_no_phase():
    audio = np.concatenate([np.zeros((RATE, 1)), tone(1.0)[:, :1], np.zeros((RATE, 1))])

    result = check(audio, RATE)

    assert result["dropouts"] == [] and result["phase"] is None
    with pytest.raises(ValueError):
        check(np.zeros((0, 2)), RATE)


def test_a_loops_length_gives_its_exact_tempo():
    from fl_studio_mcp.audio.tempo import loop_tempo

    # 2 bars at 127 BPM last 3.7795 s; a rough guess of 125 picks two bars.
    result = loop_tempo(8 * 60 / 127, 125.0)

    assert result == {"bars": 2, "bpm": 127.0, "candidates": result["candidates"]}
    assert {"bars": 4, "bpm": 254.0} not in result["candidates"]  # above the range
    assert loop_tempo(8 * 60 / 127, 99.0)["bars"] is None  # nothing near the guess
    assert loop_tempo(8 * 60 / 127, None)["bpm"] is None


def test_a_number_in_the_name_that_fits_the_length_wins():
    from fl_studio_mcp.audio.tempo import loop_tempo

    # 8 bars at 155 BPM; the heard tempo was half of it.
    result = loop_tempo(32 * 60 / 155, 77.4, numbers=(155.0, 3.0))

    assert (result["bars"], result["bpm"]) == (8, 155.0)


def test_the_files_ends_dont_ring_into_the_true_peak():
    ramp = np.linspace(0.9, -0.9, 10000)[:, None]

    assert true_peak(ramp) == pytest.approx(0.9, abs=0.01)
