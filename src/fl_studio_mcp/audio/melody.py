"""A sung or played melody (one note at a time) as notes for fl_send_notes.

The pitch is followed frame by frame (audio/pitch.py); a note lasts while the
pitch stays within half a semitone of where it started and the level doesn't
jump up (a new attack on the same pitch). Notes are then put on the beat grid.
The whole performance may sit off A4 = 440 Hz: that offset is measured and
taken out before rounding, and reported as "tuning_cents".
"""

from __future__ import annotations

import numpy as np

from fl_studio_mcp.audio.pitch import FRAME_SECONDS, HOP_SECONDS, pitch_track
from fl_studio_mcp.music.model import pitch_name

SAME_NOTE = 0.6  # semitones from the note's median before it counts as a new note
ATTACK_DB = 6.0  # a short level this far above the last frames' lowest starts a new note
ATTACK_SECONDS = 0.02  # the short level's window, at each frame's centre
ATTACK_FRAMES = 3
DIP_SAMPLES = 4  # short levels per hop looked at for a dip before an attack
MIN_FRAMES = 3  # about 70 ms: shorter pitched stretches are slides or noise
QUIETEST_VELOCITY = 0.3
VELOCITY_RANGE_DB = 30.0


def _short_levels(mono: np.ndarray, rate: int, times: np.ndarray) -> np.ndarray:
    """Level in dB of a short window at the centre of each frame: shows a new attack
    that the long pitch frames smooth over."""
    width = max(1, int(ATTACK_SECONDS * rate))
    centres = ((times + FRAME_SECONDS / 2) * rate).astype(int) - width // 2
    squares = np.concatenate([[0.0], np.cumsum(np.asarray(mono, dtype=float) ** 2)])
    starts = np.clip(centres, 0, len(mono) - width)
    return 10 * np.log10(np.maximum((squares[starts + width] - squares[starts]) / width, 1e-12))


def _dips(mono: np.ndarray, rate: int, times: np.ndarray) -> np.ndarray:
    """Lowest short level between each frame's centre and the one before: a gap
    shorter than a hop (a note played again quickly) falls between centres."""
    offsets = np.arange(DIP_SAMPLES) * HOP_SECONDS / DIP_SAMPLES
    fine = _short_levels(mono, rate, (times[:, None] - offsets).ravel())
    return fine.reshape(len(times), DIP_SAMPLES).min(axis=1)


def _segments(pitches: np.ndarray, short: np.ndarray, dips: np.ndarray) -> list[tuple[int, int]]:
    """(first frame, frame after the last) of every note."""
    segments: list[tuple[int, int]] = []
    start = None
    for i, pitch in enumerate(pitches):
        if start is not None:
            current = np.nanmedian(pitches[start:i])
            recent = dips[max(start + 1, i - ATTACK_FRAMES + 1):i + 1]
            attack = len(recent) > 0 and short[i] - recent.min() >= ATTACK_DB
            if np.isnan(pitch) or abs(pitch - current) > SAME_NOTE or attack:
                segments.append((start, i))
                start = None
        if start is None and not np.isnan(pitch):
            start = i
    if start is not None:
        segments.append((start, len(pitches)))
    return [(a, b) for a, b in segments if b - a >= MIN_FRAMES]


def extract_melody(mono: np.ndarray, rate: int, bpm: float, grid: float = 0.25,
                   start_seconds: float = 0.0) -> dict:
    """{"bpm", "tuning_cents", "notes": [{"midi", "note", "time", "duration", "velocity"}]}.

    Times are in beats from `start_seconds` into the audio, on a `grid` of beats.
    Raises ValueError when nothing pitched is found.
    """
    if not bpm > 0 or not grid > 0:
        raise ValueError("bpm and grid must be positive")
    times, pitches, levels = pitch_track(mono, rate)
    voiced = pitches[~np.isnan(pitches)]
    if not len(voiced):
        raise ValueError("No pitched sound was found: is it one voice or instrument at a time?")
    offset = float(np.median(voiced - np.rint(voiced)))  # the whole take's detune
    loudest = float(np.max(levels))
    beats_per_second = bpm / 60
    notes = []
    # A frame stands for its centre; a note spans its frames' centres, half a hop either side.
    centres = times + FRAME_SECONDS / 2
    short = _short_levels(mono, rate, times)
    for first, last in _segments(pitches, short, _dips(mono, rate, times)):
        midi = int(np.rint(np.nanmedian(pitches[first:last]) - offset))
        begin = (centres[first] - HOP_SECONDS / 2 - start_seconds) * beats_per_second
        end = (centres[last - 1] + HOP_SECONDS / 2 - start_seconds) * beats_per_second
        if end <= 0:
            continue
        begin = max(0.0, round(begin / grid) * grid)
        end = max(begin + grid, round(end / grid) * grid)
        level = float(np.max(levels[first:last])) - loudest
        velocity = 1 + (1 - QUIETEST_VELOCITY) * max(level, -VELOCITY_RANGE_DB) / VELOCITY_RANGE_DB
        notes.append({"midi": midi, "note": pitch_name(midi), "time": round(begin, 4),
                      "duration": round(end - begin, 4), "velocity": round(velocity, 2)})
    # One note at a time: two notes on one grid slot keep the later, and a note
    # ends where the next one begins.
    notes = [n for i, n in enumerate(notes)
             if i + 1 == len(notes) or n["time"] < notes[i + 1]["time"]]
    for note, following in zip(notes, notes[1:]):
        if note["time"] + note["duration"] > following["time"]:
            note["duration"] = round(following["time"] - note["time"], 4)
    return {"bpm": round(bpm, 2), "tuning_cents": int(round(offset * 100)), "notes": notes}
