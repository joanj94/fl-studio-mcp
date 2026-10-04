"""The event format shared by FL Studio's projects (.flp) and channel presets (.fst).

A file is a header chunk ("FLhd": format, channel count, PPQ) and a data chunk
("FLdt") holding a list of events. An event is an id byte and a value: ids below
64 carry one byte, below 128 two, below 192 four, and the rest a length (7 bits
a byte, lowest first) and that many bytes.

Seen in FL 2026's own projects: event 172 carries three bytes, not four. With
that one exception every project and stock preset on disk (about 100 files,
FL 12 to 2026) parses to its last byte. Seen in projects saved by the trial:
the one-, two- and four-byte values are scrambled (a channel's type read 216,
pattern numbers read 24927), while texts and data events read as they should.
So nothing here depends on those small values.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

HEADER_ID = b"FLhd"
DATA_ID = b"FLdt"
WORD = 64
DWORD = 128
TEXT = 192
# Seen live (FL 2026): this id carries three bytes although it lies in the four-byte range.
SIZE_EXCEPTIONS = {172: 3}


class FlpError(ValueError):
    """The bytes aren't an FL Studio project or preset this module can read."""


@dataclass(frozen=True)
class Event:
    id: int
    data: bytes

    @property
    def value(self) -> int:
        """The number a one-, two- or four-byte event carries."""
        return int.from_bytes(self.data, "little")

    def text(self) -> str:
        """A text event's text: UTF-16 since FL 11.5, plain bytes before."""
        raw = self.data
        if len(raw) >= 2 and len(raw) % 2 == 0 and raw[1:2] == b"\0":
            return raw.decode("utf-16-le", "replace").rstrip("\0")
        return raw.decode("latin-1").rstrip("\0")


@dataclass(frozen=True)
class FlpFile:
    format: int
    channel_count: int
    ppq: int
    events: tuple[Event, ...]


def _fixed_size(event_id: int) -> int | None:
    if event_id in SIZE_EXCEPTIONS:
        return SIZE_EXCEPTIONS[event_id]
    if event_id < WORD:
        return 1
    if event_id < DWORD:
        return 2
    if event_id < TEXT:
        return 4
    return None


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise FlpError("The file ends inside an event's length.")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value, pos


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def parse(data: bytes) -> FlpFile:
    """Split a project's or preset's bytes into its events; raises FlpError."""
    if data[:4] != HEADER_ID or len(data) < 8:
        raise FlpError("Not an FL Studio file (no FLhd header).")
    header_length = struct.unpack_from("<I", data, 4)[0]
    if header_length < 6:
        raise FlpError("The FL Studio file's header is too short.")
    file_format, channel_count, ppq = struct.unpack_from("<hHH", data, 8)
    pos = 8 + header_length
    if data[pos:pos + 4] != DATA_ID:
        raise FlpError("The FL Studio file has no FLdt chunk where it should be.")
    pos += 8
    events = []
    while pos < len(data):
        event_id = data[pos]
        pos += 1
        size = _fixed_size(event_id)
        if size is None:
            size, pos = _read_varint(data, pos)
        if pos + size > len(data):
            raise FlpError(f"Event {event_id} runs past the end of the file.")
        events.append(Event(event_id, data[pos:pos + size]))
        pos += size
    return FlpFile(file_format, channel_count, ppq, tuple(events))


def build(flp: FlpFile) -> bytes:
    """The bytes of a project or preset made of these events."""
    body = bytearray()
    for event in flp.events:
        size = _fixed_size(event.id)
        if size is None:
            body += bytes([event.id]) + _varint(len(event.data)) + event.data
        elif len(event.data) != size:
            raise FlpError(f"Event {event.id} must carry {size} bytes, not {len(event.data)}.")
        else:
            body += bytes([event.id]) + event.data
    header = struct.pack("<hHH", flp.format, flp.channel_count, flp.ppq)
    return (HEADER_ID + struct.pack("<I", len(header)) + header
            + DATA_ID + struct.pack("<I", len(body)) + bytes(body))
