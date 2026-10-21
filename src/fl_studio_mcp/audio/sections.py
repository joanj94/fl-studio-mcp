"""A song as a row of N-bar sections, and two songs compared section kind by section kind.

One number per song hides where two songs differ: a reference whose drops are all
bass and whose breaks are thin reads "balanced" over its whole length. Seen on the
user's schranz reference: 71 % of the energy at 60-250 Hz in the loud parts, and only
4-bar dips. So each song is cut into sections of `bars` bars at its own tempo,
measured (loudness and the share of energy per band, the bands of audio/analysis.py),
and its sections are sorted into "full" (within FULL_LU of the song's loud sections:
drops, choruses) and "quiet" (QUIET_LU or more below them: breaks, intros). The mix's
full sections are then compared with the reference's full ones, its quiet with its
quiet: the songs needn't be laid out alike or be as long. It tells where they
differ, not which is right.
"""

from __future__ import annotations

import math

import numpy as np

from fl_studio_mcp.audio.analysis import FREQUENCY_BANDS, k_weighted

LOUD_PERCENTILE = 90  # the song's "loud" level: this percentile of its sections' loudness
FULL_LU = 3.0
QUIET_LU = 6.0
NOTABLE_DB = 1.5
# Shares below this count as this much in a ratio: a band one song lacks altogether
# is then a large difference instead of none.
FLOOR_PERCENT = 0.1
SILENT_LUFS = -70.0
LOUDNESS_OFFSET = -0.691
MAX_SECTIONS = 256


def _lufs(power: float) -> float:
    return LOUDNESS_OFFSET + 10 * math.log10(power) if power > 0 else -120.0


def _start(mono: np.ndarray, rate: int) -> int:
    """The first sample within 60 dB of the peak: where the song begins."""
    peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
    if peak <= 0:
        return 0
    return int(np.argmax(np.abs(mono) >= peak * 1e-3))


def _bands(mono: np.ndarray, rate: int) -> dict[str, float]:
    power = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / rate)
    inside = (freqs >= FREQUENCY_BANDS[0][1]) & (freqs < FREQUENCY_BANDS[-1][2])
    total = float(power[inside].sum()) or 1.0
    return {name: round(100 * float(power[(freqs >= lo) & (freqs < hi)].sum()) / total, 1)
            for name, lo, hi in FREQUENCY_BANDS}


def sections(samples: np.ndarray, rate: int, bpm: float, bars: int = 4,
             beats_per_bar: int = 4) -> list[dict]:
    """[{"bar", "start", "lufs", "bands"}] for each whole section after the song starts.

    A last section shorter than half a section is left out. Raises ValueError.
    """
    if not 20 <= bpm <= 400:
        raise ValueError(f"bpm must be 20-400, got {bpm}")
    if not 1 <= bars <= 64:
        raise ValueError(f"bars must be 1-64, got {bars}")
    data = np.asarray(samples, dtype=float)
    data = data[:, None] if data.ndim == 1 else data
    mono = data.mean(axis=1)
    start = _start(mono, rate)
    size = int(round(bars * beats_per_bar * 60.0 / bpm * rate))
    weighted = k_weighted(data, rate)
    found = []
    for begin in range(start, len(mono), size):
        end = min(begin + size, len(mono))
        if end - begin < size / 2 or len(found) >= MAX_SECTIONS:
            break
        power = float((weighted[begin:end] ** 2).sum(axis=1).mean())
        found.append({"bar": 1 + len(found) * bars, "start": round(begin / rate, 2),
                      "lufs": round(_lufs(power), 1), "bands": _bands(mono[begin:end], rate)})
    return found


def classify(found: list[dict]) -> list[dict]:
    """The sections with "kind": full, quiet, between, or silent."""
    heard = [s["lufs"] for s in found if s["lufs"] > SILENT_LUFS]
    if not heard:
        return [{**s, "kind": "silent"} for s in found]
    loud = float(np.percentile(heard, LOUD_PERCENTILE))
    result = []
    for s in found:
        below = loud - s["lufs"]
        kind = ("silent" if s["lufs"] <= SILENT_LUFS else "full" if below <= FULL_LU
                else "quiet" if below >= QUIET_LU else "between")
        result.append({**s, "below_loud_lu": round(max(below, 0.0), 1), "kind": kind})
    return result


def _mean_bands(found: list[dict], kind: str) -> dict[str, float] | None:
    chosen = [s["bands"] for s in found if s["kind"] == kind]
    if not chosen:
        return None
    return {name: round(sum(b[name] for b in chosen) / len(chosen), 1)
            for name, _lo, _hi in FREQUENCY_BANDS}


def _runs(found: list[dict], kind: str, bars: int) -> list[int]:
    """Lengths in bars of the runs of sections of one kind."""
    runs, current = [], 0
    for s in found:
        if s["kind"] == kind:
            current += bars
        elif current:
            runs.append(current)
            current = 0
    return runs + ([current] if current else [])


def compare_sections(mix: list[dict], reference: list[dict], bars: int) -> dict:
    """Full against full, quiet against quiet (both lists from `classify`)."""
    result: dict = {}
    differences = []
    for kind in ("full", "quiet"):
        ours, theirs = _mean_bands(mix, kind), _mean_bands(reference, kind)
        entry: dict = {"mix_sections": sum(s["kind"] == kind for s in mix),
                       "reference_sections": sum(s["kind"] == kind for s in reference),
                       "mix_bands": ours, "reference_bands": theirs}
        if ours and theirs:
            entry["difference_db"] = {
                name: round(10 * math.log10(max(ours[name], FLOOR_PERCENT)
                                            / max(theirs[name], FLOOR_PERCENT)), 1)
                for name in ours}
            for name, db in sorted(entry["difference_db"].items(), key=lambda i: -abs(i[1])):
                if abs(db) >= NOTABLE_DB and len(differences) < 6:
                    differences.append(f"{kind} sections, {name}: {abs(db)} dB "
                                       f"{'more' if db > 0 else 'less'} of the energy")
        result[kind] = entry
    for name, found in (("mix", mix), ("reference", reference)):
        result.setdefault("quiet_runs_bars", {})[name] = _runs(found, "quiet", bars)
        full = [s for s in found if s["kind"] == "full"]
        quiet = [s for s in found if s["kind"] == "quiet"]
        if full and quiet:
            result.setdefault("full_over_quiet_lu", {})[name] = round(
                float(np.mean([s["lufs"] for s in full]) - np.mean([s["lufs"] for s in quiet])), 1)
    result["largest_differences"] = differences
    return result
