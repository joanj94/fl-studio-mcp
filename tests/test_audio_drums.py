"""A drum loop turned into kick/snare/hat hits, and its groove as a template."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio.drums import transcribe_drums
from fl_studio_mcp.music.model import Note
from fl_studio_mcp.music.transform import apply_operations, groove

RATE = 44100
BPM = 120.0  # a beat is 0.5 s
RNG = np.random.default_rng(7)


def kick(length=0.25):
    t = np.arange(int(length * RATE)) / RATE
    return np.sin(2 * np.pi * 55 * t) * np.exp(-t * 18)


def snare(length=0.2):
    t = np.arange(int(length * RATE)) / RATE
    noise = RNG.standard_normal(len(t))
    body = np.convolve(noise, np.ones(8) / 8, mode="same")  # rolls the top off
    return (0.6 * body + 0.5 * np.sin(2 * np.pi * 200 * t)) * np.exp(-t * 25)


def hat(length=0.06):
    t = np.arange(int(length * RATE)) / RATE
    noise = RNG.standard_normal(len(t))
    return 0.4 * np.diff(noise, prepend=0) * np.exp(-t * 80)  # mostly highs


def loop(events, seconds=2.0):
    out = np.zeros(int(seconds * RATE))
    for time, sound, level in events:
        start = int(time * RATE)
        piece = sound()[: len(out) - start]
        out[start:start + len(piece)] += level * piece
    return out


def test_a_basic_beat_is_transcribed_on_the_grid():
    beat = [(0.0, kick, 1.0), (1.0, kick, 1.0), (0.5, snare, 0.8), (1.5, snare, 0.8)]
    beat += [(t, hat, 0.5) for t in (0.25, 0.75, 1.25, 1.75)]

    result = transcribe_drums(loop(beat), RATE, bpm=BPM, grid=0.25)

    found = {(h["time"], h["drum"]) for h in result["hits"]}
    assert found == {(0.0, "kick"), (2.0, "kick"), (1.0, "snare"), (3.0, "snare"),
                     (0.5, "hat"), (1.5, "hat"), (2.5, "hat"), (3.5, "hat")}
    assert result["grids"]["kick"] == "x.......x......."
    assert {n["midi"] for n in result["notes"]} == {36, 38, 42}


def test_groove_holds_each_steps_timing_and_level():
    late = 0.03  # seconds: every off-beat hat a little late
    beat = [(0.0, kick, 1.0), (0.25 + late, hat, 0.4), (0.5, kick, 1.0),
            (0.75 + late, hat, 0.4)]

    template = transcribe_drums(loop(beat, 1.0), RATE, bpm=BPM, grid=0.5)["groove"]

    assert len(template) == 2
    assert template[0]["offset"] == pytest.approx(0, abs=0.01)
    assert template[1]["offset"] == pytest.approx(late * 2, abs=0.02)  # in beats
    assert template[1]["velocity"] < template[0]["velocity"]


def test_groove_transform_moves_and_scales_notes():
    template = [{"offset": 0.0, "velocity": 1.0}, {"offset": 0.06, "velocity": 0.5}]
    notes = [Note(60, 0, 0.5, 0.8), Note(60, 0.5, 0.5, 0.8), Note(60, 1.0, 0.5, 0.8)]

    grooved = groove(notes, template, step=0.5)

    assert [n.start for n in grooved] == pytest.approx([0, 0.56, 1.0])
    assert [n.velocity for n in grooved] == pytest.approx([0.8, 0.4, 0.8])
    assert apply_operations(notes, [{"op": "groove", "template": template, "step": 0.5}])


def test_silence_has_no_hits():
    result = transcribe_drums(np.zeros(RATE), RATE, bpm=BPM)

    assert result["hits"] == [] and result["notes"] == []


def test_groove_refuses_an_empty_template():
    with pytest.raises(ValueError):
        groove([Note(60, 0, 1)], [], step=0.25)
