"""Cutting audio at its onsets (hits, syllables, chops), without changing the sound.

Onsets come from spectral flux on short frames (a sound starting makes the spectrum
grow), picked as peaks above a share of the strongest one; each is then moved back to
the first sample where the signal rises out of what came before, so a slice starts
right at its attack. Slices run from one onset to the next.
"""

from __future__ import annotations

import numpy as np

FRAME = 1024
HOP = 256
MIN_GAP_SECONDS = 0.05
# How far before the flux peak an attack may lie, and how loud it must get to count.
ATTACK_SEARCH_SECONDS = 0.03
ATTACK_SHARE = 0.2


def _flux(mono: np.ndarray) -> np.ndarray:
    """Spectral flux between consecutive frames of the signal padded with silence before."""
    padded = np.concatenate([np.zeros(FRAME), mono, np.zeros(FRAME)])
    count = 1 + (len(padded) - FRAME) // HOP
    window = np.hanning(FRAME)
    level = np.empty((count, FRAME // 2 + 1))
    for chunk in range(0, count, 512):
        rows = np.arange(chunk, min(count, chunk + 512)) * HOP
        frames = padded[rows[:, None] + np.arange(FRAME)] * window
        level[chunk:chunk + len(rows)] = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 100)
    return np.maximum(np.diff(level, axis=0), 0).sum(axis=1)


def _attack(mono: np.ndarray, rate: int, rough: int) -> int:
    """The first sample near `rough` where the signal reaches a share of its peak there."""
    start = max(0, rough - FRAME - int(ATTACK_SEARCH_SECONDS * rate))
    end = min(len(mono), rough + FRAME)
    if end <= start:
        return max(0, min(rough, len(mono) - 1))
    span = np.abs(mono[start:end])
    above = np.nonzero(span >= ATTACK_SHARE * span.max())[0]
    return start + int(above[0]) if len(above) else rough


def find_onsets(mono: np.ndarray, rate: int, sensitivity: float = 0.7,
                min_gap: float = MIN_GAP_SECONDS) -> list[float]:
    """Onset times in seconds. Higher sensitivity (0-1) keeps quieter onsets."""
    if not 0 < sensitivity <= 1:
        raise ValueError("sensitivity must be more than 0 and at most 1")
    mono = np.asarray(mono, dtype=float)
    flux = _flux(mono)
    if len(flux) < 3 or flux.max() <= 0:
        return []
    threshold = (1 - sensitivity) * flux.max() + 0.02 * flux.max()
    reach = max(1, int(min_gap * rate / HOP))
    peaks = [
        j for j in range(len(flux))
        if flux[j] >= threshold and flux[j] == flux[max(0, j - reach):j + reach + 1].max()
    ]
    onsets: list[float] = []
    for j in peaks:
        rough = (j + 1) * HOP - FRAME // 2  # centre of the later frame, unpadded
        time = _attack(mono, rate, max(0, rough)) / rate
        if not onsets or time - onsets[-1] >= min_gap:
            onsets.append(round(time, 4))
    return onsets


def slice_points(onsets: list[float], total: float,
                 min_length: float = MIN_GAP_SECONDS) -> list[tuple[float, float]]:
    """(start, end) of each slice: onset to onset, the last to the end.

    Audio before the first onset is a slice of its own; a slice shorter than
    `min_length` is joined to the one before it.
    """
    starts = sorted({round(t, 4) for t in onsets if 0 <= t < total})
    if not starts or starts[0] > 1e-3:
        starts = [0.0, *starts]
    kept = [starts[0]]
    for start, following in zip(starts[1:], [*starts[2:], total]):
        if following - start >= min_length:
            kept.append(start)
    if len(kept) > 1 and total - kept[-1] < min_length:
        kept.pop()
    return [(start, end) for start, end in zip(kept, [*kept[1:], total])]
