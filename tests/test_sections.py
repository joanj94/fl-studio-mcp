"""A song cut into N-bar sections, and two songs compared drop with drop, break with break."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from fl_studio_mcp.audio.sections import classify, compare_sections, sections
from fl_studio_mcp.tools import register_audio_tools
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector
from tests.test_audio import write_wav

RATE = 22050
SECTION = 8.0  # 4 bars at 120 BPM


def _tone(hz: float, seconds: float, level: float) -> np.ndarray:
    return level * np.sin(2 * np.pi * hz * np.arange(int(seconds * RATE)) / RATE)


def song(layout: str, highs: float = 0.0) -> np.ndarray:
    """D = a loud bass section (with `highs` of 8 kHz in it), b = a quiet mid one."""
    parts = []
    for kind in layout:
        if kind == "D":
            parts.append(_tone(100, SECTION, 0.5) + _tone(8000, SECTION, highs))
        else:
            parts.append(_tone(1000, SECTION, 0.05))
    return np.concatenate(parts)


def test_sections_are_cut_at_the_tempo_and_sorted_by_kind():
    found = classify(sections(song("DDbbDD"), RATE, 120))

    assert [s["bar"] for s in found] == [1, 5, 9, 13, 17, 21]
    assert [s["kind"] for s in found] == ["full", "full", "quiet", "quiet", "full", "full"]
    assert found[0]["bands"]["bass"] > 95 and found[2]["bands"]["mid"] > 95


def test_silence_before_the_song_is_skipped():
    late = np.concatenate([np.zeros(RATE), song("Db")])
    assert [s["start"] for s in sections(late, RATE, 120)] == [1.0, 9.0]


def test_full_is_compared_with_full_whatever_the_layout():
    mix = classify(sections(song("DDbbDD"), RATE, 120))
    reference = classify(sections(song("DbDDDb", highs=0.2), RATE, 120))

    result = compare_sections(mix, reference, 4)

    assert result["full"]["difference_db"]["high"] < -10  # the reference's drops have highs
    assert result["largest_differences"][0].startswith("full sections, high:")
    assert result["quiet_runs_bars"] == {"mix": [8], "reference": [4, 4]}
    # 20 dB apart in level; K-weighting counts 100 Hz about 2 dB below 1 kHz
    assert result["full_over_quiet_lu"]["mix"] == pytest.approx(18.2, abs=0.5)


@pytest.mark.parametrize(("bpm", "bars"), [(10, 4), (120, 0)])
def test_bad_tempo_or_length_is_refused(bpm, bars):
    with pytest.raises(ValueError):
        sections(song("D"), RATE, bpm, bars)


@pytest.fixture
def audio_tools(monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(connection, "get_connection", lambda: ScriptedConnection())
    collector = ToolCollector()
    register_audio_tools(collector)
    return collector.tools


def test_the_tool_compares_two_files(audio_tools, tmp_path):
    mix = write_wav(tmp_path / "mix.wav", np.column_stack([song("DDbbDD")] * 2), RATE, 3, 32)
    reference = write_wav(tmp_path / "ref.wav",
                          np.column_stack([song("DbDD", highs=0.2)] * 2), RATE, 3, 32)

    result = audio_tools["audio_compare_sections"](str(mix), str(reference), bpm=120,
                                                   reference_bpm=120)

    assert result["mix"]["bpm"] == 120 and len(result["mix"]["sections"]) == 6
    assert "bands" not in result["mix"]["sections"][0]
    assert result["full"]["mix_sections"] == 4 and result["full"]["reference_sections"] == 3
    assert "error" in audio_tools["audio_compare_sections"](str(mix), str(tmp_path / "no.wav"),
                                                            bpm=120, reference_bpm=120)
