"""EQ moves that bring a mix's tonal balance toward a reference track.

Both are reduced to a long-term spectrum in third-octave bands (power averaged over
the whole file, masking.py's bands). The level difference between the two as a
whole is taken out (the mean over the bands), so only the shape counts, and the
difference is smoothed over three neighbouring bands: a move is an octave wide,
not one band. The largest differences become EQ bands, each kept apart from the
others, at `strength` of the difference and at most MAX_GAIN_DB: a reference is
another song, so a full match is rarely meant. The lowest and highest bands are
shelves.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from fl_studio_mcp.audio.decode import load_audio
from fl_studio_mcp.audio.masking import _band_levels, _bands, _mono

MAX_GAIN_DB = 6.0
MIN_DIFFERENCE_DB = 1.5  # smoothed differences below this aren't worth a band
SPACING_BANDS = 3  # chosen bands lie at least this many third-octaves apart
MAX_BANDS = 7  # Fruity Parametric EQ 2 has seven
# A file whose loudest band stays below this (dB; a full-scale sine reads about +66) is silent.
SILENT_DB = -60.0


def long_term_spectrum(samples: np.ndarray, rate: int) -> dict[float, float]:
    """{band centre Hz: level dB} averaged over the whole file."""
    mono = _mono(samples)
    bands = _bands(rate)
    if len(mono) < 4096:
        raise ValueError("The audio is too short to measure")
    levels = _band_levels(mono, rate, bands)
    power = (10 ** (levels / 10)).mean(axis=0)
    return {round(centre, 1): float(10 * np.log10(p + 1e-20))
            for (_, centre, _), p in zip(bands, power)}


def file_spectrum(path: Path) -> dict[float, float]:
    """long_term_spectrum of an audio file (any format load_audio reads)."""
    samples, rate = load_audio(Path(path))
    return long_term_spectrum(samples, rate)


def match_files(path: Path, reference: Path, max_bands: int = 4,
                strength: float = 0.7) -> dict:
    """match_eq of two audio files. Raises ValueError/OSError."""
    return match_eq(file_spectrum(path), file_spectrum(reference), max_bands, strength)


def match_eq(mix: dict[float, float], reference: dict[float, float], max_bands: int = 4,
             strength: float = 0.7) -> dict:
    """EQ bands from two long-term spectra (long_term_spectrum's output).

    Returns {"curve": [{"hz", "difference_db"}] (reference minus mix, smoothed),
    "bands": [{"hz", "gain_db", "type"}] lowest first}.
    """
    if not 1 <= max_bands <= MAX_BANDS:
        raise ValueError(f"max_bands must be 1-{MAX_BANDS}, got {max_bands}")
    if not 0 < strength <= 1:
        raise ValueError(f"strength must be more than 0 and at most 1, got {strength}")
    centres = sorted(set(mix) & set(reference))
    if len(centres) < SPACING_BANDS:
        raise ValueError("The files share too few frequency bands to compare")
    if max(mix[c] for c in centres) < SILENT_DB or max(reference[c] for c in centres) < SILENT_DB:
        raise ValueError("One of the files is silent")
    raw = np.array([reference[c] - mix[c] for c in centres])
    raw -= raw.mean()
    smooth = np.convolve(np.pad(raw, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    curve = [{"hz": c, "difference_db": round(float(d), 1)} for c, d in zip(centres, smooth)]
    chosen: list[int] = []
    for index in np.argsort(-np.abs(smooth), kind="stable"):
        if abs(smooth[index]) < MIN_DIFFERENCE_DB or len(chosen) >= max_bands:
            break
        if all(abs(int(index) - c) >= SPACING_BANDS for c in chosen):
            chosen.append(int(index))
    bands = []
    for index in sorted(chosen):
        gain = max(-MAX_GAIN_DB, min(MAX_GAIN_DB, float(smooth[index]) * strength))
        kind = ("Low shelf" if index == 0 else
                "High shelf" if index == len(centres) - 1 else "Peaking")
        bands.append({"hz": round(centres[index]), "gain_db": round(gain, 1), "type": kind})
    return {"curve": curve, "bands": bands}
