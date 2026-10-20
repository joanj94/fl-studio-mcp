"""Chords over time from audio: one chord per span of beats.

For each span (a bar by default) the spectrum between BASS_HZ and TOP_HZ is folded
into the twelve pitch classes (a chroma vector) and compared with major and minor
triad templates on every root; the best fit names the span. A span much quieter
than the loudest one is "N" (no chord). It reads the harmony of a whole mix, so a
loud drum or a lead's melody can sway a span: the confidence says by how much the
best chord won.
"""

from __future__ import annotations

import numpy as np

FRAME = 8192
HOP = 2048
BASS_HZ = 80.0  # below this sits the kick more than the harmony
TOP_HZ = 5000.0
SILENT_SHARE = 0.01  # a span with less energy than this share of the loudest is "N"
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
TEMPLATES = {"": (0, 4, 7), "m": (0, 3, 7)}


def _chroma_frames(mono: np.ndarray, rate: int) -> np.ndarray:
    """Chroma (frames x 12) of overlapping frames."""
    count = max(0, 1 + (len(mono) - FRAME) // HOP)
    window = np.hanning(FRAME)
    freqs = np.fft.rfftfreq(FRAME, 1 / rate)
    band = (freqs >= BASS_HZ) & (freqs <= TOP_HZ)
    classes = (np.round(12 * np.log2(freqs[band] / 440.0)).astype(int) + 69) % 12
    chroma = np.zeros((count, 12))
    for chunk in range(0, count, 128):
        rows = np.arange(chunk, min(count, chunk + 128)) * HOP
        power = np.abs(np.fft.rfft(mono[rows[:, None] + np.arange(FRAME)] * window, axis=1))
        power = power[:, band] ** 2
        for pc in range(12):
            chroma[chunk:chunk + len(rows), pc] = power[:, classes == pc].sum(axis=1)
    return chroma


def _templates() -> list[tuple[str, np.ndarray]]:
    found = []
    for root in range(12):
        for suffix, intervals in TEMPLATES.items():
            vector = np.zeros(12)
            vector[[(root + i) % 12 for i in intervals]] = 1.0
            found.append((NOTE_NAMES[root] + suffix, vector / np.linalg.norm(vector)))
    return found


def detect_chords(mono: np.ndarray, rate: int, bpm: float, beats_per_chord: float = 4.0,
                  start_seconds: float = 0.0) -> list[dict]:
    """[{"time" (beats from start_seconds), "chord" ("Am", "F", "N"), "confidence"}]."""
    if bpm <= 0 or beats_per_chord <= 0:
        raise ValueError("bpm and beats_per_chord must be positive")
    mono = np.asarray(mono, dtype=float)
    mono = mono.mean(axis=1) if mono.ndim == 2 else mono
    mono = mono[int(max(0.0, start_seconds) * rate):]
    chroma = _chroma_frames(mono, rate)
    span_seconds = beats_per_chord * 60.0 / bpm
    spans = int(round(len(mono) / rate / span_seconds))
    sums = []
    for index in range(spans):
        # A frame belongs to the span its centre lies in.
        first = int(np.ceil((index * span_seconds * rate - FRAME / 2) / HOP))
        last = int(np.ceil(((index + 1) * span_seconds * rate - FRAME / 2) / HOP))
        sums.append(chroma[max(0, first):max(0, last)].sum(axis=0))
    loudest = max((float(s.sum()) for s in sums), default=0.0)
    templates = _templates()
    found = []
    for index, total in enumerate(sums):
        time = round(index * beats_per_chord, 6)
        if loudest <= 0 or total.sum() < SILENT_SHARE * loudest:
            found.append({"time": time, "chord": "N", "confidence": 0.0})
            continue
        vector = np.sqrt(total)  # amplitude, so one loud note doesn't drown the rest
        vector = vector / np.linalg.norm(vector)
        scores = sorted(((float(vector @ t), name) for name, t in templates), reverse=True)
        found.append({"time": time, "chord": scores[0][1],
                      "confidence": round(scores[0][0] - scores[1][0], 3)})
    return found
