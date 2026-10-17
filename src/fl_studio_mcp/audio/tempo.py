"""Tempo of a piece of audio, from how regularly its onsets come.

An onset envelope (how much the spectrum grows from one short frame to the
next) is compared with itself at every delay in the tempo range; the beat is
the delay at which it repeats best, counting its multiples too (a beat that
repeats also repeats every two and four beats). Half and double the tempo fit
the same onsets, so they are listed as alternatives with their scores: which
one is meant can depend on the music.
"""

from __future__ import annotations

import numpy as np

FRAME = 2048
HOP_SECONDS = 0.01
MIN_BPM = 60.0
MAX_BPM = 240.0
MIN_SECONDS = 4.0  # shorter audio has too few beats to tell a tempo
MULTIPLES = 4
# The scores lean gently toward this tempo, one octave away weighing this much less.
CENTER_BPM = 120.0
OCTAVE_WEIGHT = 0.9
# Half the beat scoring at least this share of the beat's score makes it the beat.
FASTER_SHARE = 0.88  # measured: a 85 BPM song with 8th-note hats scored 0.85, 160 BPM 0.91


def onset_envelope(mono: np.ndarray, rate: int) -> tuple[np.ndarray, float]:
    """(spectral flux per frame, frames per second)."""
    hop = max(1, int(round(HOP_SECONDS * rate)))
    count = 1 + (len(mono) - FRAME) // hop
    if count < 2:
        return np.zeros(0), rate / hop
    window = np.hanning(FRAME)
    starts = np.arange(count) * hop
    magnitude = np.empty((count, FRAME // 2 + 1))
    for chunk in range(0, count, 512):  # a few hundred frames at a time
        rows = starts[chunk:chunk + 512]
        frames = mono[rows[:, None] + np.arange(FRAME)] * window
        magnitude[chunk:chunk + len(rows)] = np.abs(np.fft.rfft(frames, axis=1))
    level = np.log1p(magnitude * 100)
    flux = np.maximum(np.diff(level, axis=0), 0).sum(axis=1)
    return flux - flux.mean(), rate / hop


def estimate_tempo(mono: np.ndarray, rate: int, low: float = MIN_BPM,
                   high: float = MAX_BPM) -> dict | None:
    """{"bpm", "confidence", "alternatives": [{"bpm", "score"}]}, or None when too short.

    "confidence" is how far the best score stands above the typical one (0-1).
    """
    if len(mono) / rate < MIN_SECONDS:
        return None
    envelope, fps = onset_envelope(np.asarray(mono, dtype=float), rate)
    if not len(envelope) or not np.any(envelope):
        return None
    size = len(envelope)
    spectrum = np.fft.rfft(envelope, 2 * size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum))[:size]
    correlation /= correlation[0] if correlation[0] > 0 else 1.0

    def score(lag: float) -> float:
        total = 0.0
        for k in range(1, MULTIPLES + 1):
            at = lag * k
            if at >= size - 1:
                break
            i = int(at)
            total += (correlation[i] + (correlation[i + 1] - correlation[i]) * (at - i)) / k
        return total

    def weight(bpm: float) -> float:
        return OCTAVE_WEIGHT ** abs(np.log2(bpm / CENTER_BPM))

    lags = np.arange(60 * fps / high, 60 * fps / low, 0.25)
    if not len(lags):
        return None
    scores = np.array([score(lag) * weight(60 * fps / lag) for lag in lags])
    best = int(np.argmax(scores))
    lag = float(lags[best])
    # Onsets as strong half-way between the beats are beats too (a kick on every
    # beat repeats every two beats just as well): take the faster tempo then.
    while 60 * fps / (lag / 2) <= high and score(lag / 2) >= FASTER_SHARE * score(lag):
        lag /= 2
    bpm = 60 * fps / lag
    top = score(lag) * weight(bpm)
    spread = float(np.median(scores))
    confidence = (top - spread) / (abs(top) + abs(spread) or 1.0)
    alternatives = []
    for factor in (0.5, 2.0, 2 / 3, 1.5):
        other = bpm * factor
        if low <= other <= high:
            alternatives.append({
                "bpm": round(float(other), 1),
                "score": round(float(score(60 * fps / other) * weight(other) / top), 2),
            })
    return {
        "bpm": round(float(bpm), 1),
        "confidence": round(float(max(0.0, min(1.0, confidence))), 2),
        "alternatives": alternatives,
    }
