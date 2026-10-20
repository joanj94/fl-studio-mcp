"""A drum loop as hits: kick, snare or hat per onset, on a grid, and the loop's groove.

Onsets come from audio/slicing.py. The first ANALYSIS_SECONDS after each one are
sorted by where their energy lies: mostly below LOW_HZ is a kick, mostly above
HIGH_HZ a hat, anything else a snare (claps and toms land there too). Each hit is put
on the nearest grid step; how far it was from it, and how loud, make the groove: per
step position within a beat, the mean offset and the mean level against the loudest
position. Meant for a drum loop or a drum stem; a full mix's other parts get in the way.
"""

from __future__ import annotations

import math

import numpy as np

from fl_studio_mcp.audio.slicing import find_onsets

ANALYSIS_SECONDS = 0.05
LOW_HZ = 150.0
HIGH_HZ = 6000.0
DOMINANT_SHARE = 0.5
# Seen with synthetic drums: a pure low kick has far less spectral flux than noise hits,
# so drum loops need a higher onset sensitivity than slicing (0.7 missed every kick).
GM_NOTES = {"kick": 36, "snare": 38, "hat": 42}
BEATS_PER_BAR = 4


def _classify(segment: np.ndarray, rate: int) -> str:
    power = np.abs(np.fft.rfft(segment * np.hanning(len(segment)))) ** 2
    freqs = np.fft.rfftfreq(len(segment), 1 / rate)
    total = power.sum()
    if total <= 0:
        return "snare"
    if power[freqs < LOW_HZ].sum() / total >= DOMINANT_SHARE:
        return "kick"
    if power[freqs > HIGH_HZ].sum() / total >= DOMINANT_SHARE:
        return "hat"
    return "snare"


def transcribe_drums(mono: np.ndarray, rate: int, bpm: float, grid: float = 0.25,
                     start_seconds: float = 0.0, sensitivity: float = 0.9) -> dict:
    """Hits, notes (General MIDI kick 36, snare 38, hat 42), grid strings and the groove.

    Returns {"bpm", "grid", "hits": [{"time" (beats), "drum", "velocity"}], "notes"
    (fl_send_notes format), "grids": {drum: "x...x..."} over whole bars, "groove":
    [{"offset" (beats), "velocity" (factor)}] per step of a beat}.
    """
    if bpm <= 0 or grid <= 0:
        raise ValueError("bpm and grid must be positive")
    mono = np.asarray(mono, dtype=float)
    mono = mono.mean(axis=1) if mono.ndim == 2 else mono
    mono = mono[int(max(0.0, start_seconds) * rate):]
    width = max(16, int(ANALYSIS_SECONDS * rate))
    # (step time, drum) -> (level, offset from the step in beats)
    found: dict[tuple[float, str], tuple[float, float]] = {}
    for onset in find_onsets(mono, rate, sensitivity):
        start = int(onset * rate)
        segment = mono[start:start + width]
        if len(segment) < 16:
            continue
        drum = _classify(segment, rate)
        level = float(np.abs(segment).max())
        beats = onset * bpm / 60.0
        step_time = round(round(beats / grid) * grid, 6)
        key = (step_time, drum)
        if key not in found or level > found[key][0]:
            found[key] = (level, beats - step_time)
    loudest = max((level for level, _ in found.values()), default=0.0)
    hits = sorted(
        ({"time": time, "drum": drum, "velocity": round(level / loudest, 3) if loudest else 0}
         for (time, drum), (level, _) in found.items()),
        key=lambda h: (h["time"], GM_NOTES[h["drum"]]))
    notes = [{"midi": GM_NOTES[h["drum"]], "time": h["time"], "duration": grid,
              "velocity": max(0.01, h["velocity"])} for h in hits]
    total_beats = len(mono) / rate * bpm / 60.0
    bars = max(1, math.ceil(total_beats / BEATS_PER_BAR - 1e-6))
    steps = int(round(bars * BEATS_PER_BAR / grid))
    grids = {}
    for drum in GM_NOTES:
        marks = ["."] * steps
        for h in hits:
            if h["drum"] == drum and 0 <= round(h["time"] / grid) < steps:
                marks[round(h["time"] / grid)] = "x"
        if "x" in marks:
            grids[drum] = "".join(marks)
    return {"bpm": bpm, "grid": grid, "hits": hits, "notes": notes, "grids": grids,
            "groove": _groove(found, grid)}


def _groove(found: dict, grid: float) -> list[dict]:
    positions = max(1, int(round(1 / grid)))
    offsets: list[list[float]] = [[] for _ in range(positions)]
    levels: list[list[float]] = [[] for _ in range(positions)]
    for (time, _), (level, offset) in found.items():
        position = int(round(time / grid)) % positions
        offsets[position].append(offset)
        levels[position].append(level)
    means = [float(np.mean(v)) if v else 0.0 for v in levels]
    top = max(means) or 1.0
    return [{"offset": round(float(np.mean(o)), 4) if o else 0.0,
             "velocity": round(m / top, 3) if lv else 1.0}
            for o, m, lv in zip(offsets, means, levels)]
