"""Measurements of a piece of audio: loudness, peaks, spectrum balance, stereo width, key, pitch.

Everything here describes the sound; judging it against a style is left to the
caller. Samples are arrays of shape (frames, channels) in -1.0..1.0.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from fl_studio_mcp.audio.pitch import estimate_pitch

# ITU-R BS.1770 loudness: 400 ms blocks overlapping by 75%, gated at -70 LUFS
# and at 10 LU below the level of the blocks that pass that first gate.
BLOCK_SECONDS = 0.4
BLOCK_OVERLAP = 0.75
ABSOLUTE_GATE_LUFS = -70.0
RELATIVE_GATE_LU = -10.0
LOUDNESS_OFFSET = -0.691

# K-weighting filter (BS.1770), as two filter stages defined by analogue parameters
# so they can be built for any sample rate.
SHELF_GAIN_DB = 3.999843853973347
SHELF_Q = 0.7071752369554196
SHELF_HZ = 1681.974450955533
SHELF_BAND_EXPONENT = 0.4996667741545416
HIGHPASS_Q = 0.5003270373238773
HIGHPASS_HZ = 38.13547087602444

FREQUENCY_BANDS = (  # name, from Hz, up to Hz
    ("sub", 20.0, 60.0),
    ("bass", 60.0, 250.0),
    ("low_mid", 250.0, 500.0),
    ("mid", 500.0, 2000.0),
    ("high_mid", 2000.0, 6000.0),
    ("high", 6000.0, 20000.0),
)

# Krumhansl-Kessler key profiles: how strongly each scale degree belongs to a key.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)
PITCH_CLASSES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
KEY_MIN_HZ = 55.0
KEY_MAX_HZ = 2000.0

SILENCE_DB = -60.0
CLIP_LEVEL = 0.999
MAX_TIMELINE_POINTS = 64
# The whole file is held in memory several times over while it is measured.
MAX_ANALYSIS_SECONDS = 20 * 60
FLOOR_DB = -120.0


def _db(power: float) -> float:
    """Decibels for a power ratio, floored so silence isn't -infinity."""
    return max(10.0 * np.log10(power), FLOOR_DB) if power > 0 else FLOOR_DB


def _biquad_response(b: tuple, a: tuple, omega: np.ndarray) -> np.ndarray:
    """Power gain of a two-pole filter at angular frequencies `omega` (radians/sample)."""
    z1, z2 = np.exp(-1j * omega), np.exp(-2j * omega)
    return np.abs((b[0] + b[1] * z1 + b[2] * z2) / (a[0] + a[1] * z1 + a[2] * z2)) ** 2


def k_weighting_gain(frequencies: np.ndarray, rate: int) -> np.ndarray:
    """Power gain of the K-weighting filter at each frequency (Hz)."""
    omega = 2 * np.pi * frequencies / rate

    # The standard only lists coefficients for 48 kHz; these formulas reproduce
    # them and give the same filter at other rates.
    k = np.tan(np.pi * SHELF_HZ / rate)
    high = 10 ** (SHELF_GAIN_DB / 20)
    band = high ** SHELF_BAND_EXPONENT
    shelf = _biquad_response(
        (high + band * k / SHELF_Q + k * k, 2 * (k * k - high), high - band * k / SHELF_Q + k * k),
        (1 + k / SHELF_Q + k * k, 2 * (k * k - 1), 1 - k / SHELF_Q + k * k),
        omega,
    )

    k = np.tan(np.pi * HIGHPASS_HZ / rate)
    norm = 1 + k / HIGHPASS_Q + k * k
    highpass = _biquad_response(
        (1.0, -2.0, 1.0), (1.0, 2 * (k * k - 1) / norm, (1 - k / HIGHPASS_Q + k * k) / norm), omega
    )
    return shelf * highpass


def k_weighted(samples: np.ndarray, rate: int) -> np.ndarray:
    """The samples after K-weighting, filtered in the frequency domain."""
    gain = np.sqrt(k_weighting_gain(np.fft.rfftfreq(len(samples), 1 / rate), rate))
    weighted = np.empty_like(samples)
    for channel in range(samples.shape[1]):  # one at a time keeps the peak memory down
        weighted[:, channel] = np.fft.irfft(
            np.fft.rfft(samples[:, channel]) * gain, n=len(samples)
        )
    return weighted


def _block_powers(weighted: np.ndarray, rate: int, seconds: float, overlap: float) -> np.ndarray:
    """Mean square summed over channels, for each block of `seconds`."""
    size = int(round(seconds * rate))
    step = max(1, int(round(size * (1 - overlap))))
    if len(weighted) < size:
        return np.empty(0)
    # Running sums make every block a subtraction, however many blocks there are.
    sums = np.concatenate([[0.0], np.cumsum((weighted ** 2).sum(axis=1))])
    starts = np.arange(0, len(weighted) - size + 1, step)
    return (sums[starts + size] - sums[starts]) / size


def _loudness(power: float) -> float:
    return LOUDNESS_OFFSET + _db(power) if power > 0 else FLOOR_DB


def integrated_loudness(weighted: np.ndarray, rate: int) -> float | None:
    """Programme loudness in LUFS (BS.1770, gated) of K-weighted samples.

    None if the audio is shorter than one block or silent.
    """
    powers = _block_powers(weighted, rate, BLOCK_SECONDS, BLOCK_OVERLAP)
    absolute_gate = 10 ** ((ABSOLUTE_GATE_LUFS - LOUDNESS_OFFSET) / 10)
    loud = powers[powers > absolute_gate]
    if not len(loud):
        return None
    relative_gate = loud.mean() * 10 ** (RELATIVE_GATE_LU / 10)
    return float(_loudness(loud[loud > relative_gate].mean()))


def loudness_timeline(weighted: np.ndarray, rate: int, window: float) -> list[dict]:
    """Ungated loudness of consecutive windows of `window` seconds (widened if too many).

    The last window may be shorter; one under a loudness block long is left out.
    """
    duration = len(weighted) / rate
    size = max(1, int(round(max(window, duration / MAX_TIMELINE_POINTS) * rate)))
    energy = (weighted ** 2).sum(axis=1)
    timeline = []
    for start in range(0, len(weighted), size):
        block = energy[start:start + size]
        if len(block) < size and len(block) < BLOCK_SECONDS * rate and timeline:
            break
        timeline.append({
            "start": round(start / rate, 2),
            "lufs": round(float(_loudness(float(block.mean()))), 1),
        })
    return timeline


def power_spectrum(mono: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Power at each frequency of a mono signal, and those frequencies in Hz."""
    return np.abs(np.fft.rfft(mono)) ** 2, np.fft.rfftfreq(len(mono), 1 / rate)


def band_balance(mono: np.ndarray, rate: int) -> dict[str, dict]:
    """Share of the energy in each frequency band, in percent and in dB below the total."""
    return _band_balance(*power_spectrum(mono, rate))


def _band_balance(power: np.ndarray, frequencies: np.ndarray) -> dict[str, dict]:
    in_range = (frequencies >= FREQUENCY_BANDS[0][1]) & (frequencies < FREQUENCY_BANDS[-1][2])
    total = power[in_range].sum()
    balance = {}
    for name, low, high in FREQUENCY_BANDS:
        share = power[(frequencies >= low) & (frequencies < high)].sum() / total if total else 0.0
        balance[name] = {
            "hz": [low, high],
            "percent": round(float(share) * 100, 1),
            "db": round(float(_db(share)), 1),
        }
    return balance


def estimate_key(mono: np.ndarray, rate: int) -> dict | None:
    """Most likely key from the energy of each pitch class; None for silence.

    "confidence" is the correlation with the key's profile (1.0 = perfect fit);
    a close "alternative" means the key is ambiguous (relative keys often are).
    """
    return _estimate_key(*power_spectrum(mono, rate))


def _estimate_key(power: np.ndarray, frequencies: np.ndarray) -> dict | None:
    audible = (frequencies >= KEY_MIN_HZ) & (frequencies <= KEY_MAX_HZ)
    if not audible.any() or power[audible].sum() <= 0:
        return None
    midi = np.rint(69 + 12 * np.log2(frequencies[audible] / 440.0)).astype(int)
    chroma = np.bincount(midi % 12, weights=power[audible], minlength=12)
    chroma = np.sqrt(chroma)  # amplitude, so one loud note doesn't drown the rest
    if chroma.std() == 0:
        return None  # every pitch class equally strong (noise): no key to find

    scores = []
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        for tonic in range(12):
            fit = np.corrcoef(chroma, np.roll(profile, tonic))[0, 1]
            scores.append((float(fit), f"{PITCH_CLASSES[tonic]} {mode}"))
    scores.sort(reverse=True)
    return {
        "key": scores[0][1],
        "confidence": round(scores[0][0], 2),
        "alternative": scores[1][1],
        "alternative_confidence": round(scores[1][0], 2),
    }


def stereo_image(samples: np.ndarray) -> dict | None:
    """Left/right correlation (1 = mono, 0 = unrelated, -1 = out of phase) and width."""
    if samples.shape[1] < 2:
        return None
    left, right = samples[:, 0], samples[:, 1]
    mid_power = float(np.mean(((left + right) / 2) ** 2))
    side_power = float(np.mean(((left - right) / 2) ** 2))
    both = float(np.sqrt(np.mean(left ** 2) * np.mean(right ** 2)))
    return {
        "correlation": round(float(np.mean(left * right)) / both, 2) if both else None,
        "side_to_mid_db": round(_db(side_power / mid_power), 1) if mid_power else None,
    }


def _silence(mono: np.ndarray, rate: int) -> tuple[float, float]:
    """Seconds of silence at the start and at the end."""
    audible = np.flatnonzero(np.abs(mono) > 10 ** (SILENCE_DB / 20))
    if not len(audible):
        return len(mono) / rate, len(mono) / rate
    return audible[0] / rate, (len(mono) - 1 - audible[-1]) / rate


def _plain(value: Any) -> Any:
    """The same data with numpy's numbers turned into Python's, so it can be sent as JSON."""
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def analyze(samples: np.ndarray, rate: int, window: float = 4.0) -> dict:
    """All measurements of a piece of audio as one JSON-friendly dict."""
    if samples.ndim != 2 or not len(samples):
        raise ValueError("The audio is empty")
    if len(samples) / rate > MAX_ANALYSIS_SECONDS:
        raise ValueError(
            f"The audio is {len(samples) / rate / 60:.0f} minutes long; "
            f"at most {MAX_ANALYSIS_SECONDS // 60} minutes can be analysed"
        )
    mono = samples.mean(axis=1)
    peak = float(np.max(np.abs(samples)))
    rms_power = float(np.mean(samples ** 2))
    peak_db = round(_db(peak ** 2), 2)
    rms_db = round(_db(rms_power), 2)
    weighted = k_weighted(samples, rate)
    loudness = integrated_loudness(weighted, rate)
    lead, trail = _silence(mono, rate)
    power, frequencies = power_spectrum(mono, rate)
    return _plain({
        "duration_seconds": round(len(samples) / rate, 2),
        "sample_rate": rate,
        "channels": samples.shape[1],
        "loudness_lufs": None if loudness is None else round(loudness, 1),
        "peak_dbfs": peak_db,
        "rms_dbfs": rms_db,
        "crest_factor_db": round(peak_db - rms_db, 1),
        "clipped_samples": int(np.count_nonzero(np.abs(samples) >= CLIP_LEVEL)),
        "silence_seconds": {"start": round(lead, 2), "end": round(trail, 2)},
        "frequency_balance": _band_balance(power, frequencies),
        "stereo": stereo_image(samples),
        "key": _estimate_key(power, frequencies),
        "pitch": estimate_pitch(mono, rate),
        "loudness_over_time": loudness_timeline(weighted, rate, window),
    })
