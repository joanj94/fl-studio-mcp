"""Which note a sound plays, and how far it is from that note.

The pitch is followed through short frames by normalised autocorrelation (the
McLeod pitch method), which finds the fundamental even when overtones are
louder or the fundamental itself is missing. A key estimate tells what a whole
part fits; this tells what one sound (a drum, a preset playing one note) is
tuned to.
"""

from __future__ import annotations

import numpy as np

from fl_studio_mcp.music.model import pitch_name

MIN_HZ = 27.5  # A0: below this nothing is heard as a note
MAX_HZ = 2000.0
FRAME_SECONDS = 0.093  # long enough for three periods of the lowest pitch
HOP_SECONDS = 0.023
# A frame counts as pitched when it repeats itself this well (1.0 = exactly).
MIN_CLARITY = 0.8
# The first repeat this close to the best one is the period; a later, slightly
# better one is a multiple of it (an octave or more too low).
FIRST_PEAK_SHARE = 0.9
# Frames this far below the loudest one are the sound dying away, not the sound.
QUIET_DB = -40.0
MAX_NOTES_LISTED = 4
SEMITONES = 12
A4_MIDI = 69
A4_HZ = 440.0


def _frame_pitch(frame: np.ndarray, rate: int) -> tuple[float, float] | None:
    """(Hz, clarity) of one frame, or None if nothing in it repeats."""
    size = len(frame)
    spectrum = np.fft.rfft(frame, 2 * size)
    correlation = np.fft.irfft(spectrum * np.conj(spectrum))[:size]
    squares = np.concatenate([[0.0], np.cumsum(frame ** 2)])
    lags = np.arange(size)
    # Energy of the two stretches each lag compares, so a fading sound still scores 1.
    energy = squares[size - lags] + (squares[size] - squares[lags])
    nsdf = np.where(energy > 0, 2 * correlation / np.maximum(energy, 1e-30), 0.0)

    negative = np.flatnonzero(nsdf < 0)
    if not len(negative):
        return None
    first = max(int(negative[0]), int(rate / MAX_HZ))  # past the peak at lag 0
    last = min(size - 2, int(rate / MIN_HZ))
    if last - first < 2:
        return None
    inner = nsdf[first:last + 1]
    peaks = np.flatnonzero((inner[1:-1] > inner[:-2]) & (inner[1:-1] >= inner[2:])) + first + 1
    peaks = peaks[nsdf[peaks] > 0]
    if not len(peaks):
        return None
    lag = int(peaks[nsdf[peaks] >= FIRST_PEAK_SHARE * nsdf[peaks].max()][0])

    # A parabola through the peak and its neighbours places it between samples.
    before, at, after = nsdf[lag - 1], nsdf[lag], nsdf[lag + 1]
    curve = before - 2 * at + after
    shift = 0.5 * (before - after) / curve if curve else 0.0
    return rate / (lag + shift), float(at - 0.25 * (before - after) * shift)


def _pitched_frames(mono: np.ndarray, rate: int) -> tuple[np.ndarray, int]:
    """MIDI pitch (with fraction) of every clearly pitched frame, and how many frames sound."""
    mono = np.asarray(mono, dtype=float)
    size = int(round(FRAME_SECONDS * rate))
    hop = max(1, int(round(HOP_SECONDS * rate)))
    if len(mono) < size:
        return np.empty(0), 0
    starts = np.arange(0, len(mono) - size + 1, hop)
    squares = np.concatenate([[0.0], np.cumsum(mono ** 2)])
    powers = (squares[starts + size] - squares[starts]) / size
    if powers.max() <= 0:
        return np.empty(0), 0
    sounding = starts[powers >= powers.max() * 10 ** (QUIET_DB / 10)]
    pitches = []
    for start in sounding:
        found = _frame_pitch(mono[start:start + size], rate)
        if found and found[1] >= MIN_CLARITY:
            pitches.append(A4_MIDI + SEMITONES * float(np.log2(found[0] / A4_HZ)))
    return np.array(pitches), len(sounding)


def _most_common(notes: np.ndarray) -> int:
    values, counts = np.unique(notes, return_counts=True)
    return int(values[np.argmax(counts)])


def estimate_pitch(mono: np.ndarray, rate: int) -> dict | None:
    """The note a sound plays most of the time; None if it has no clear pitch.

    "cents" is how far that note is played from equal temperament (A4 = 440 Hz),
    "share" the part of the pitched time spent on it, "class_share" the part
    spent on that note in any octave (a sound with several oscillators is
    heard in several), "ends_on" the note held in the last quarter of the
    sound (it differs from "note" for a pitch that glides), "voiced" the part
    of the sounding time that is pitched at all, and "notes" the most played
    notes.
    """
    pitches, sounding = _pitched_frames(mono, rate)
    if not len(pitches):
        return None
    nearest = np.rint(pitches).astype(int)
    notes, counts = np.unique(nearest, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    note = int(notes[order[0]])
    played = float(np.median(pitches[nearest == note]))
    return {
        "note": pitch_name(note),
        "midi": note,
        "hz": round(A4_HZ * 2 ** ((played - A4_MIDI) / SEMITONES), 2),
        "cents": int(round((played - note) * 100)),
        "share": round(float(counts[order[0]]) / len(pitches), 2),
        "class_share": round(float(np.mean((nearest - note) % SEMITONES == 0)), 2),
        "ends_on": pitch_name(_most_common(nearest[-max(1, len(nearest) // 4):])),
        "voiced": round(len(pitches) / sounding, 2),
        "notes": [
            {"note": pitch_name(int(notes[i])), "share": round(float(counts[i]) / len(pitches), 2)}
            for i in order[:MAX_NOTES_LISTED]
        ],
    }


def semitones_to_pitch_class(midi: int, pitch_class: int) -> int:
    """The smallest move, in semitones, that brings a note onto a pitch class (-6..5)."""
    return (pitch_class - midi + SEMITONES // 2) % SEMITONES - SEMITONES // 2
