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


# --- Reading ----------------------------------------------------------------------------

DEFAULT_BPM = 120.0
# Data bytes after a channel message's status, by its high nibble.
_DATA_BYTES = {0x8: 2, 0x9: 2, 0xA: 2, 0xB: 2, 0xC: 1, 0xD: 1, 0xE: 2}


def _read_vlq(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if position >= len(data):
            raise ValueError("The MIDI file ends in the middle of an event")
        byte = data[position]
        position += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, position
    raise ValueError("The MIDI file has a broken length")


def _chunks_of(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks = []
    position = 0
    while position + 8 <= len(data):
        kind = data[position:position + 4]
        size = struct.unpack(">I", data[position + 4:position + 8])[0]
        body = data[position + 8:position + 8 + size]
        if len(body) < size:
            raise ValueError("The MIDI file is cut off")
        chunks.append((kind, body))
        position += 8 + size
    return chunks


def _events(body: bytes):
    """(tick, status, data bytes) of a track's channel and meta events (meta: status 0xFF)."""
    position, tick, running = 0, 0, None
    while position < len(body):
        delta, position = _read_vlq(body, position)
        tick += delta
        if position >= len(body):
            raise ValueError("The MIDI file ends in the middle of an event")
        status = body[position]
        if status == 0xFF:
            kind = body[position + 1] if position + 1 < len(body) else 0
            size, position = _read_vlq(body, position + 2)
            yield tick, 0xFF, bytes([kind]) + body[position:position + size]
            position += size
            continue
        if status in (0xF0, 0xF7):  # sysex: skipped
            size, position = _read_vlq(body, position + 1)
            position += size
            continue
        if status & 0x80:
            running = status
            position += 1
        elif running is None:
            raise ValueError("The MIDI file has data without a status byte")
        count = _DATA_BYTES.get(running >> 4, 0)
        if position + count > len(body):
            raise ValueError("The MIDI file ends in the middle of an event")
        yield tick, running, body[position:position + count]
        position += count


def parse_midi(data: bytes) -> dict:
    """A standard MIDI file's notes, per track and MIDI channel, in fl_send_notes format.

    Returns {"ppq", "bpm" (the first tempo), "beats_per_bar", "tracks": [{"name",
    "channel", "drums", "notes": [{"midi", "time", "duration", "velocity"}]}]}
    with times in beats. Raises ValueError for anything that isn't a MIDI file.
    """
    chunks = _chunks_of(data)
    if not chunks or chunks[0][0] != b"MThd" or len(chunks[0][1]) < 6:
        raise ValueError("Not a standard MIDI file")
    _, _, division = struct.unpack(">HHh", chunks[0][1][:6])
    if division <= 0:
        raise ValueError("MIDI files timed in SMPTE frames aren't supported")
    bpm, beats_per_bar, tracks = None, 4, []
    for number, (kind, body) in enumerate(c for c in chunks[1:] if c[0] == b"MTrk"):
        name = f"Track {number + 1}"
        sounding: dict[tuple[int, int], list[tuple[int, int]]] = {}
        notes: dict[int, list[dict]] = {}
        for tick, status, payload in _events(body):
            if status == 0xFF:
                meta, content = payload[0], payload[1:]
                if meta == 0x03 and content:
                    name = content.decode("utf-8", "replace")
                elif meta == 0x51 and len(content) == 3 and bpm is None:
                    bpm = 60_000_000 / int.from_bytes(content, "big")
                elif meta == 0x58 and content:
                    beats_per_bar = content[0]
                continue
            channel, message = status & 0x0F, status >> 4
            if message not in (0x8, 0x9):
                continue
            pitch, velocity = payload[0], payload[1]
            if message == 0x9 and velocity > 0:
                sounding.setdefault((channel, pitch), []).append((tick, velocity))
            elif sounding.get((channel, pitch)):
                start, velocity = sounding[(channel, pitch)].pop(0)
                notes.setdefault(channel, []).append({
                    "midi": pitch, "time": round(start / division, 6),
                    "duration": round((tick - start) / division, 6),
                    "velocity": round(velocity / 127, 4),
                })
        for channel in sorted(notes):
            tracks.append({"name": name, "channel": channel, "drums": channel == DRUM_CHANNEL,
                           "notes": sorted(notes[channel], key=lambda n: (n["time"], n["midi"]))})
    return {"ppq": division, "bpm": round(bpm or DEFAULT_BPM, 3),
            "beats_per_bar": beats_per_bar, "tracks": tracks}


def read_midi(path: Path) -> dict:
    """parse_midi of a file. Raises ValueError or OSError."""
    return parse_midi(Path(path).read_bytes())
