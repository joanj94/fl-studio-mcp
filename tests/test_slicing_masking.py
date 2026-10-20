"""Slicing audio at its onsets, writing WAVs, and masking between two parts."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio.masking import masking
from fl_studio_mcp.audio.slicing import find_onsets, slice_points
from fl_studio_mcp.audio.wav import read_wav, write_wav

RATE = 44100


def hits(times: list[float], length: float = 2.0, pitch: float = 220.0) -> np.ndarray:
    """Decaying tones starting at `times` (seconds)."""
    t = np.arange(int(length * RATE)) / RATE
    out = np.zeros_like(t)
    for start in times:
        local = t - start
        on = local >= 0
        out[on] += np.sin(2 * np.pi * pitch * local[on]) * np.exp(-local[on] * 12)
    return out * 0.5


def tone(freqs: list[float], seconds: float = 3.0, level: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return sum(level * np.sin(2 * np.pi * f * t) for f in freqs)


def test_onsets_are_found_where_the_hits_start():
    onsets = find_onsets(hits([0.0, 0.5, 1.0, 1.5]), RATE)

    assert len(onsets) == 4
    assert np.allclose(onsets, [0.0, 0.5, 1.0, 1.5], atol=0.02)


def test_sensitivity_drops_quiet_hits():
    signal = hits([0.0, 1.0]) + 0.05 * hits([0.5, 1.5])

    loud_only = find_onsets(signal, RATE, sensitivity=0.3)

    assert np.allclose(loud_only, [0.0, 1.0], atol=0.02)


def test_slice_points_run_from_onset_to_onset_and_the_end():
    points = slice_points([0.0, 0.5, 1.0], total=2.0, min_length=0.1)

    assert points == [(0.0, 0.5), (0.5, 1.0), (1.0, 2.0)]


def test_slices_shorter_than_the_minimum_join_the_previous_one():
    points = slice_points([0.0, 0.5, 0.52, 1.0], total=2.0, min_length=0.1)

    assert points == [(0.0, 0.52), (0.52, 1.0), (1.0, 2.0)]


def test_audio_before_the_first_onset_is_its_own_slice():
    assert slice_points([0.3, 1.0], total=2.0, min_length=0.1)[0] == (0.0, 0.3)


def test_wav_round_trip(tmp_path):
    stereo = np.stack([hits([0.0]), -hits([0.0])], axis=1)

    write_wav(tmp_path / "a.wav", stereo, RATE)
    samples, rate = read_wav(tmp_path / "a.wav")

    assert rate == RATE
    assert samples.shape == stereo.shape
    assert np.allclose(samples, stereo, atol=1e-6)


def test_masking_finds_the_shared_band_only():
    a = tone([100, 1000])
    b = tone([100, 5000])

    result = masking(a, b, RATE)

    worst = result["conflicts"][0]
    assert 70 <= worst["low_hz"] <= 100 <= worst["high_hz"] <= 140
    assert all(not (c["low_hz"] <= 1000 <= c["high_hz"]) for c in result["conflicts"])
    assert all(not (c["low_hz"] <= 5000 <= c["high_hz"]) for c in result["conflicts"])


def test_parts_that_do_not_share_a_band_have_no_conflict():
    assert masking(tone([100]), tone([3000]), RATE)["conflicts"] == []


def test_masking_needs_the_same_rate_and_some_sound():
    with pytest.raises(ValueError):
        masking(tone([100], 0.01), tone([100], 0.01), RATE)


@pytest.fixture
def audio_tools():
    from fl_studio_mcp.tools import audio
    from tests.fakes import ToolCollector

    collector = ToolCollector()
    audio.register_audio_tools(collector)
    return collector.tools


def test_audio_slice_writes_one_wav_per_slice(audio_tools, tmp_path):
    write_wav(tmp_path / "chops.wav", hits([0.0, 0.5, 1.0, 1.5]), RATE)

    result = audio_tools["audio_slice"](str(tmp_path / "chops.wav"),
                                        folder=str(tmp_path / "out"))

    assert [s["file"] for s in result["slices"]] == [f"chops 0{i}.wav" for i in range(1, 5)]
    first, rate = read_wav(tmp_path / "out" / "chops 01.wav")
    assert rate == RATE and len(first) == pytest.approx(0.5 * RATE, abs=0.03 * RATE)


def test_audio_slice_refuses_too_many_slices(audio_tools, tmp_path):
    write_wav(tmp_path / "chops.wav", hits([0.0, 0.5, 1.0, 1.5]), RATE)

    result = audio_tools["audio_slice"](str(tmp_path / "chops.wav"), max_slices=2,
                                        folder=str(tmp_path / "out"))

    assert "max_slices" in result["error"]
    assert not (tmp_path / "out").exists()


def test_audio_masking_tool(audio_tools, tmp_path):
    write_wav(tmp_path / "a.wav", tone([100, 1000]), RATE)
    write_wav(tmp_path / "b.wav", tone([100, 5000]), RATE)

    result = audio_tools["audio_masking"](str(tmp_path / "a.wav"), str(tmp_path / "b.wav"))

    assert result["conflicts"][0]["centre_hz"] == pytest.approx(100, rel=0.05)


def test_silent_parts_mask_nothing():
    silence = np.zeros(3 * RATE)
    assert masking(silence, silence, RATE)["conflicts"] == []
    assert masking(silence, tone([100]), RATE)["conflicts"] == []
