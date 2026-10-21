"""What one kick hit is made of: punch, where its energy sits, its tone and its tail.

Measured on a single rendered hit (`fl_kick_lab`). The numbers describe the sound
and judge nothing: which balance is right depends on the music, not on this.

- punch: the first PUNCH_SECONDS' peak over the body's level (BODY window), in dB.
  A click or a hard transient reads high, a soft or swelling attack low.
- bands: shares of the energy over the hit's first BANDS_SECONDS: sub (below 70 Hz),
  thump (70-250 Hz), mid (250 Hz-2 kHz), high (above). Seen on the user's hard kicks:
  a "deep" kick was mostly sub; the one they liked had about 8 % sub, 50 % thump
  and 35 % mid.
- tone: the strongest frequency of the body between TONE_RANGE, as a note. For a
  distorted kick this is often an overtone (seen live: the fifth above the root).
- tail: how long the hit stays within TAIL_DB of its loudest moment.
"""

from __future__ import annotations

import math

import numpy as np

PUNCH_SECONDS = 0.015
BODY = (0.05, 0.25)  # seconds after the start
BANDS_SECONDS = 0.3
BAND_EDGES = {"sub": (0.0, 70.0), "thump": (70.0, 250.0), "mid": (250.0, 2000.0),
              "high": (2000.0, float("inf"))}
TONE_RANGE = (30.0, 1000.0)
TAIL_DB = 40.0
FRAME_SECONDS = 0.005
START_DB = 40.0  # the hit starts where it first comes within this of its peak
SILENT = 1e-5
TONE_PADDING = 8  # the body's FFT is zero-padded to this many times its length (~0.6 Hz bins)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _note(hz: float) -> dict:
    midi = 69 + 12 * math.log2(hz / 440.0)
    nearest = round(midi)
    return {"hz": round(hz, 1), "note": f"{NOTE_NAMES[nearest % 12]}{nearest // 12 - 1}",
            "cents": round((midi - nearest) * 100)}


def _db(ratio: float) -> float:
    return round(20 * math.log10(max(ratio, 1e-12)), 1)


def kick_profile(samples: np.ndarray, rate: int) -> dict | None:
    """The measures above for one hit (mono or frames x channels); None if silent."""
    mono = np.asarray(samples, dtype=float)
    mono = mono.mean(axis=1) if mono.ndim == 2 else mono
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak < SILENT:
        return None
    start = int(np.argmax(np.abs(mono) >= peak * 10 ** (-START_DB / 20)))
    hit = mono[start:]

    frame = max(1, int(FRAME_SECONDS * rate))
    count = len(hit) // frame
    level = (np.sqrt(np.mean(hit[:count * frame].reshape(count, frame) ** 2, axis=1))
             if count else np.array([peak]))
    above = np.flatnonzero(level >= level.max() * 10 ** (-TAIL_DB / 20))
    tail = (int(above[-1]) + 1) * FRAME_SECONDS

    punch = float(np.max(np.abs(hit[:max(1, int(PUNCH_SECONDS * rate))])))
    body = hit[int(BODY[0] * rate):int(BODY[1] * rate)]
    body_rms = float(np.sqrt(np.mean(body ** 2))) if len(body) else 0.0

    part = hit[:max(2, int(BANDS_SECONDS * rate))]
    power = np.abs(np.fft.rfft(part * np.hanning(len(part)))) ** 2
    freqs = np.fft.rfftfreq(len(part), 1 / rate)
    total = float(power.sum()) or 1.0
    bands = {name: round(100 * float(power[(freqs >= lo) & (freqs < hi)].sum()) / total, 1)
             for name, (lo, hi) in BAND_EDGES.items()}

    result = {
        "tail_seconds": round(tail, 3),
        "peak_dbfs": _db(peak),
        "punch_db": _db(punch / body_rms) if body_rms > 0 else None,
        "bands_percent": bands,
        "tone": None,
    }
    if len(body) >= 2:
        size = len(body) * TONE_PADDING
        spectrum = np.abs(np.fft.rfft(body * np.hanning(len(body)), n=size))
        body_freqs = np.fft.rfftfreq(size, 1 / rate)
        inside = (body_freqs >= TONE_RANGE[0]) & (body_freqs <= TONE_RANGE[1])
        if inside.any() and spectrum[inside].max() > 0:
            result["tone"] = _note(float(body_freqs[inside][np.argmax(spectrum[inside])]))
    return result
