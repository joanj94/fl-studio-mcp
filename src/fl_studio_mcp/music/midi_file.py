"""Standard MIDI files (type 1) from notes in fl_send_notes format, written by hand.

Track 0 holds the tempo and time signature; each part is a track of its own on
its own MIDI channel (channel 10, index 9, is left to parts marked "drums").
"""

from __future__ import annotations

import struct
from pathlib import Path

PPQ = 480
DRUM_CHANNEL = 9
SHORTEST = PPQ // 8  # a step hit (duration 0) sounds for a 32nd note
MAX_TRACKS = 64


def _vlq(value: int) -> bytes:
    """MIDI variable-length quantity."""
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack(">I", len(body)) + body


def _meta(kind: int, data: bytes) -> bytes:
    return bytes([0xFF, kind]) + _vlq(len(data)) + data


def _track(events: list[tuple[int, int, bytes]]) -> bytes:
    """events: (tick, order at the same tick, message); ends with End of Track."""
    body = b""
    now = 0
    for tick, _, message in sorted(events, key=lambda e: (e[0], e[1])):
        body += _vlq(tick - now) + message
        now = tick
    return _chunk(b"MTrk", body + b"\x00" + _meta(0x2F, b""))


def _number(note: dict, key: str, default: float) -> float:
    value = note.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"A note's {key!r} must be a number, got {value!r}")
    return float(value)


def midi_bytes(tracks: list[dict], bpm: float, beats_per_bar: int = 4) -> bytes:
    """The file's bytes. Each track: {"name", "notes": [...], "drums"?: bool}.

    Raises ValueError on bad input.
    """
    if not 10 <= bpm <= 999:
        raise ValueError(f"bpm must be 10-999, got {bpm}")
    if not tracks or len(tracks) > MAX_TRACKS:
        raise ValueError(f"Give 1-{MAX_TRACKS} tracks")
    tempo = round(60_000_000 / bpm)
    conductor = [
        (0, 0, _meta(0x51, tempo.to_bytes(3, "big"))),
        (0, 1, _meta(0x58, bytes([beats_per_bar, 2, 24, 8]))),
    ]
    chunks = [_track(conductor)]
    melodic = [c for c in range(16) if c != DRUM_CHANNEL]
    for number, track in enumerate(tracks):
        name = str(track.get("name") or f"Track {number + 1}")
        channel = DRUM_CHANNEL if track.get("drums") else melodic[number % len(melodic)]
        events = [(0, 0, _meta(0x03, name.encode("utf-8")))]
        for note in track.get("notes", []):
            pitch = int(_number(note, "midi", -1))
            if not 0 <= pitch <= 127:
                raise ValueError(f"Track {name!r}: note pitch {pitch} is outside 0-127")
            start = round(_number(note, "time", 0) * PPQ)
            if start < 0:
                raise ValueError(f"Track {name!r}: a note starts before 0")
            length = max(SHORTEST, round(_number(note, "duration", 0) * PPQ))
            velocity = max(1, min(127, round(_number(note, "velocity", 0.8) * 127)))
            # At one tick, note-offs come before note-ons, so a repeated note sounds again.
            events.append((start, 2, bytes([0x90 | channel, pitch, velocity])))
            events.append((start + length, 1, bytes([0x80 | channel, pitch, 0])))
        chunks.append(_track(events))
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), PPQ))
    return header + b"".join(chunks)


def write_midi(path: Path, tracks: list[dict], bpm: float, beats_per_bar: int = 4) -> dict:
    """Write a .mid file; returns {"path", "tracks", "notes"}. Raises ValueError/OSError."""
    path = Path(path)
    if path.suffix.lower() not in (".mid", ".midi"):
        raise ValueError(f"The file must end in .mid, got {path.name}")
    data = midi_bytes(tracks, bpm, beats_per_bar)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"path": str(path), "tracks": len(tracks),
            "notes": sum(len(t.get("notes", [])) for t in tracks)}
