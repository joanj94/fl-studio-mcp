"""What a sample sounds like, as a few numbers, and which samples sound alike.

Each file is reduced to a short vector from its first ANALYSED_SECONDS: length,
spectral centroid, the share of energy in six bands, attack and decay times and
spectral flatness (tone vs noise). Similar samples are the nearest vectors after
each feature is scaled by its spread over the candidates, so no feature counts
more because of its unit. Names play no part: a kick named "Boom 03" is found
next to the other kicks.
"""

from __future__ import annotations

import numpy as np

ANALYSED_SECONDS = 3.0
FRAME_SECONDS = 0.01
BAND_EDGES = (100.0, 300.0, 1000.0, 3000.0, 8000.0)  # six bands
DECAY_DB = 20.0
NAMES = ("length", "brightness", "sub", "low", "low_mid", "mid", "high_mid", "high",
         "attack", "decay", "noisiness")
# Brightness counts a little more. Seen on the user's packs: with length at half
# weight a 127 BPM synth loop came up among a snare's matches; at 1 it didn't, and
# kicks and hats still found only kicks and hats.
WEIGHTS = np.array([1.0, 1.5, 1, 1, 1, 1, 1, 1, 1, 1, 1])
SILENT = 1e-6


def features(samples: np.ndarray, rate: int) -> list[float] | None:
    """The feature vector (NAMES order) of a sound, or None if it is silent or empty."""
    samples = np.asarray(samples, dtype=float)
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    if len(mono) < 2 or float(np.max(np.abs(mono))) < SILENT:
        return None
    length = len(mono) / rate
    part = mono[:int(ANALYSED_SECONDS * rate)]
    spectrum = np.abs(np.fft.rfft(part * np.hanning(len(part)))) ** 2
    freqs = np.fft.rfftfreq(len(part), 1 / rate)
    total = float(spectrum.sum()) or 1.0
    centroid = float((spectrum * freqs).sum() / total)
    edges = (0.0, *BAND_EDGES, float("inf"))
    shares = [float(spectrum[(freqs >= lo) & (freqs < hi)].sum() / total)
              for lo, hi in zip(edges, edges[1:])]
    flatness = float(np.exp(np.mean(np.log(spectrum + 1e-20))) / (np.mean(spectrum) + 1e-20))
    attack, decay = _envelope_times(part, rate)
    return [float(np.log(length)), float(np.log(max(centroid, 20.0))), *shares,
            float(np.log(attack)), float(np.log(decay)), flatness]


def _envelope_times(mono: np.ndarray, rate: int) -> tuple[float, float]:
    """(seconds to the peak, seconds from the peak until DECAY_DB below it), from
    FRAME_SECONDS frames; the decay is at most the analysed length."""
    frame = max(1, int(FRAME_SECONDS * rate))
    count = len(mono) // frame
    if count == 0:
        return FRAME_SECONDS, FRAME_SECONDS
    level = np.sqrt(np.mean(mono[:count * frame].reshape(count, frame) ** 2, axis=1))
    peak = int(np.argmax(level))
    below = np.flatnonzero(level[peak:] < level[peak] * 10 ** (-DECAY_DB / 20))
    decay_frames = int(below[0]) if len(below) else len(level) - peak
    return (peak + 1) * FRAME_SECONDS, max(1, decay_frames) * FRAME_SECONDS


def nearest(reference: list[float], candidates: dict[str, list[float]],
            limit: int) -> list[tuple[str, float]]:
    """[(name, distance)] of the candidates closest to the reference, closest first.

    Each feature is divided by its standard deviation over the candidates (and the
    reference), then weighted by WEIGHTS.
    """
    if not candidates:
        return []
    names = list(candidates)
    matrix = np.array([candidates[name] for name in names])
    spread = np.vstack([matrix, reference]).std(axis=0)
    spread[spread < 1e-9] = 1.0
    distance = np.sqrt((((matrix - reference) / spread * WEIGHTS) ** 2).sum(axis=1))
    order = np.argsort(distance, kind="stable")[:limit]
    return [(names[i], round(float(distance[i]), 3)) for i in order]


def describe(vector: list[float]) -> dict:
    """The vector in readable units."""
    named = dict(zip(NAMES, vector))
    return {"seconds": round(float(np.exp(named["length"])), 3),
            "brightness_hz": round(float(np.exp(named["brightness"]))),
            "bands": {name: round(named[name], 3) for name in NAMES[2:8]},
            "attack_ms": round(float(np.exp(named["attack"])) * 1000),
            "decay_ms": round(float(np.exp(named["decay"])) * 1000),
            "noisiness": round(named["noisiness"], 3)}
