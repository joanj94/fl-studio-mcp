"""Where two parts compete for the same frequencies (masking), from their own renders.

Both parts are cut into third-octave bands frame by frame. A band conflicts in a
frame when both parts are active there (no more than ACTIVE_RANGE_DB below their own
loudest band) and within CONFLICT_DB of each other: neither stands out, so both blur.
The result says where and how often, never which part should give way.
"""

from __future__ import annotations

import numpy as np

FRAME = 4096
HOP = 2048
ACTIVE_RANGE_DB = 40.0
CONFLICT_DB = 6.0
MIN_SHARE = 0.25  # a band conflicting in fewer of the frames is left out
LOWEST_HZ = 30.0
HIGHEST_HZ = 16000.0
MIN_FRAMES = 4
# A part whose loudest band stays below this (dB; a full-scale sine reads about +66) is silent.
SILENCE_DB = -60.0


def _bands(rate: int) -> list[tuple[float, float, float]]:
    """(low, centre, high) of the third-octave bands within the audio's range."""
    bands = []
    for k in range(-15, 13):
        centre = 1000.0 * 2 ** (k / 3)
        low, high = centre * 2 ** (-1 / 6), centre * 2 ** (1 / 6)
        if low >= LOWEST_HZ and high <= min(HIGHEST_HZ, rate / 2):
            bands.append((low, centre, high))
    return bands


def _band_levels(mono: np.ndarray, rate: int, bands) -> np.ndarray:
    """Level in dB of each band in each frame: (frames, bands)."""
    count = 1 + (len(mono) - FRAME) // HOP
    window = np.hanning(FRAME)
    freqs = np.fft.rfftfreq(FRAME, 1 / rate)
    masks = [(freqs >= low) & (freqs < high) for low, _, high in bands]
    levels = np.empty((count, len(bands)))
    for chunk in range(0, count, 256):
        rows = np.arange(chunk, min(count, chunk + 256)) * HOP
        power = np.abs(np.fft.rfft(mono[rows[:, None] + np.arange(FRAME)] * window, axis=1)) ** 2
        for b, mask in enumerate(masks):
            levels[chunk:chunk + len(rows), b] = 10 * np.log10(power[:, mask].sum(axis=1) + 1e-20)
    return levels


def _mono(samples: np.ndarray) -> np.ndarray:
    samples = np.asarray(samples, dtype=float)
    return samples.mean(axis=1) if samples.ndim == 2 else samples


def masking(a: np.ndarray, b: np.ndarray, rate: int) -> dict:
    """Bands where parts `a` and `b` mask each other, worst first.

    Returns {"conflicts": [{"low_hz", "centre_hz", "high_hz", "share" (of the
    frames), "louder" ("a", "b" or "even"), "difference_db", "level_db" (how far
    below the parts' loudest band)}], "frames"}.
    """
    a, b = _mono(a), _mono(b)
    length = min(len(a), len(b))
    if (length - FRAME) // HOP + 1 < MIN_FRAMES:
        raise ValueError("The parts are too short to compare (a second at least)")
    bands = _bands(rate)
    if not bands:
        raise ValueError(f"A sample rate of {rate} Hz leaves no bands to compare")
    level_a = _band_levels(a[:length], rate, bands)
    level_b = _band_levels(b[:length], rate, bands)
    active_a = (level_a > level_a.max() - ACTIVE_RANGE_DB) & (level_a.max() > SILENCE_DB)
    active_b = (level_b > level_b.max() - ACTIVE_RANGE_DB) & (level_b.max() > SILENCE_DB)
    conflict = active_a & active_b & (np.abs(level_a - level_b) <= CONFLICT_DB)
    frames = len(level_a)
    found = []
    for index, (low, centre, high) in enumerate(bands):
        share = conflict[:, index].sum() / frames
        if share < MIN_SHARE:
            continue
        rows = conflict[:, index]
        difference = float(np.mean(level_a[rows, index] - level_b[rows, index]))
        louder = "even" if abs(difference) < 1 else ("a" if difference > 0 else "b")
        # How loud the band is in the louder part, relative to that part's loudest band.
        level = float(np.mean(np.maximum(level_a[rows, index] - level_a.max(),
                                         level_b[rows, index] - level_b.max())))
        found.append({"low_hz": round(low, 1), "centre_hz": round(centre, 1),
                      "high_hz": round(high, 1), "share": round(float(share), 3),
                      "louder": louder, "difference_db": round(difference, 1),
                      "level_db": round(level, 1)})
    found.sort(key=lambda c: (-c["share"], -c["level_db"]))
    return {"conflicts": found, "frames": frames}
