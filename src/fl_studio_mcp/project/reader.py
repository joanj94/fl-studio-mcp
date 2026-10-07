"""What a saved project holds that FL's scripting API can't tell: automation and clips.

FL's API can't read automation clips or playlist clips; a saved project (.flp,
also FL's autosaves) has both. Layouts, seen live in FL 2026 projects and
stock presets:

- Every channel starts with event 64; its name is the first event 203 after it.
  Channels come in rack order, so the n-th event 64 is channel n.
- An automation clip's points are event 234 in its channel: a 21-byte head
  whose bytes 17-20 count the points, then 24 bytes a point (float64 offset
  from the point before it in beats, float64 value 0-1, float32 tension, 4
  bytes). The first point's offset is its position.
- What an automation clip controls is event 227, one per link: the clip's
  channel at bytes 2-3 and FL's REC event id of the target at bytes 8-11.
  Seen live for mixer track volume and pan: the id's low 16 bits are
  REC_Mixer_Vol's (0x1FC0) or REC_Mixer_Pan's (0x1FC1), bits 22-27 the mixer
  track and the top four bits 7 (0x71401FC0: track 5's volume).
- The playlist of an arrangement is event 233 after the arrangement's name
  (event 241): one record per clip, 88 bytes in FL 2026, 60 in FL 21, 32
  before. A record starts with the clip's position in ticks (uint32), 20480
  (uint16), the item (uint16: 20480 + n for pattern n, else a channel's index:
  an audio or automation clip), its length in ticks (uint32) and its track as
  500 - track (uint16).
- A playlist track's name is event 239 after the track's own event 238, whose
  first four bytes are the track's number.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from fl_studio_mcp.project.flp import FlpFile

CHANNEL_NEW = 64
CHANNEL_NAME = 203
AUTOMATION_POINTS = 234
AUTOMATION_LINK = 227
ARRANGEMENT_NAME = 241
PLAYLIST = 233
TRACK_DATA = 238
TRACK_NAME = 239
# After these the channels are over (mixer and arrangement data follow).
END_OF_CHANNELS = (ARRANGEMENT_NAME, PLAYLIST, TRACK_DATA)

POINTS_COUNT_OFFSET = 17
POINTS_OFFSET = 21
POINT_SIZE = 24
PATTERN_BASE = 20480
PLAYLIST_RECORD_SIZES = (88, 60, 32)
TRACK_BASE = 500

# FL's REC event ids (midi.REC_*): a mixer track's parameters, in the id's low 16 bits.
REC_MIXER_PARAMETERS = {0x1FC0: "volume", 0x1FC1: "pan", 0x1FC2: "stereo separation"}
REC_MIXER_KIND = 0x7  # the id's top four bits for a mixer track's parameter (seen live)
REC_MIXER_TRACK_SHIFT = 22
REC_MIXER_TRACK_MASK = 0x3F


@dataclass(frozen=True)
class AutomationPoint:
    beat: float  # from the clip's start
    value: float  # 0-1: the parameter's whole range
    tension: float


@dataclass(frozen=True)
class Clip:
    position: int  # ticks from the song's start
    length: int  # ticks
    track: int  # playlist track, 1-based
    pattern: int | None  # the pattern a pattern clip plays
    channel: int | None  # the channel of an audio or automation clip


@dataclass(frozen=True)
class Arrangement:
    name: str
    clips: tuple[Clip, ...]


@dataclass(frozen=True)
class Project:
    ppq: int
    channels: tuple[str, ...]  # names, in rack order
    automation: dict[int, tuple[AutomationPoint, ...]]  # channel -> points
    links: dict[int, tuple[int, ...]]  # automation channel -> REC event ids it controls
    arrangements: tuple[Arrangement, ...]
    track_names: dict[int, str] = field(default_factory=dict)


def describe_target(rec_id: int) -> dict:
    """What a REC event id controls, as far as it is known; always keeps the raw id."""
    target: dict = {"rec_id": f"0x{rec_id:08X}"}
    parameter = REC_MIXER_PARAMETERS.get(rec_id & 0xFFFF)
    if parameter is not None and rec_id >> 28 == REC_MIXER_KIND:
        track = (rec_id >> REC_MIXER_TRACK_SHIFT) & REC_MIXER_TRACK_MASK
        target.update(kind="mixer track", track=track, parameter=parameter)
    return target


def parse_points(data: bytes) -> tuple[AutomationPoint, ...]:
    """The points of an automation clip's event 234."""
    if len(data) < POINTS_OFFSET:
        return ()
    count = struct.unpack_from("<I", data, POINTS_COUNT_OFFSET)[0]
    if POINTS_OFFSET + count * POINT_SIZE > len(data):
        return ()
    points, beat = [], 0.0
    for i in range(count):
        offset, value, tension = struct.unpack_from("<ddf", data, POINTS_OFFSET + i * POINT_SIZE)
        beat += offset
        points.append(AutomationPoint(beat, value, tension))
    return tuple(points)


def _record_size(data: bytes) -> int | None:
    for size in PLAYLIST_RECORD_SIZES:
        if data and len(data) % size == 0 and all(
            struct.unpack_from("<H", data, start + 4)[0] == PATTERN_BASE
            for start in range(0, len(data), size)
        ):
            return size
    return None


def parse_playlist(data: bytes) -> tuple[Clip, ...]:
    """The clips of an event 233; empty if its records can't be told apart."""
    size = _record_size(data)
    if size is None:
        return ()
    clips = []
    for start in range(0, len(data), size):
        position, _base, item, length, track = struct.unpack_from("<IHHIH", data, start)
        is_pattern = item > PATTERN_BASE
        clips.append(Clip(
            position=position, length=length, track=TRACK_BASE - track,
            pattern=item - PATTERN_BASE if is_pattern else None,
            channel=None if is_pattern else item,
        ))
    return tuple(sorted(clips, key=lambda c: (c.track, c.position)))


def _link(data: bytes) -> tuple[int, int] | None:
    if len(data) < 12:
        return None
    return struct.unpack_from("<H", data, 2)[0], struct.unpack_from("<I", data, 8)[0]


@dataclass
class _Reading:
    """What has been read so far; only `read_project` sees it."""

    names: list[str] = field(default_factory=list)
    named: list[bool] = field(default_factory=list)
    automation: dict[int, tuple[AutomationPoint, ...]] = field(default_factory=dict)
    links: dict[int, list[int]] = field(default_factory=dict)
    arrangements: list[Arrangement] = field(default_factory=list)
    track_names: dict[int, str] = field(default_factory=dict)
    in_channels: bool = False
    arrangement_name: str | None = None
    track: int | None = None


def _channel_event(reading: _Reading, event_id: int, data: bytes, text) -> None:
    channel = len(reading.names) - 1
    if event_id == CHANNEL_NAME and not reading.named[channel]:
        reading.names[channel] = text()
        reading.named[channel] = True
    elif event_id == AUTOMATION_POINTS:
        reading.automation[channel] = parse_points(data)


def _song_event(reading: _Reading, event_id: int, data: bytes, text) -> None:
    if event_id == AUTOMATION_LINK and (link := _link(data)) is not None:
        reading.links.setdefault(link[0], []).append(link[1])
    elif event_id == ARRANGEMENT_NAME:
        reading.arrangement_name = text()
    elif event_id == PLAYLIST:
        name = reading.arrangement_name or f"Arrangement {len(reading.arrangements) + 1}"
        reading.arrangements.append(Arrangement(name, parse_playlist(data)))
        reading.arrangement_name = None
    elif event_id == TRACK_DATA and len(data) >= 4:
        reading.track = struct.unpack_from("<I", data, 0)[0]
    elif event_id == TRACK_NAME and reading.track is not None:
        reading.track_names[reading.track] = text()


def read_project(flp: FlpFile) -> Project:
    reading = _Reading()
    for event in flp.events:
        if event.id == CHANNEL_NEW:
            reading.names.append("")
            reading.named.append(False)
            reading.in_channels = True
        elif event.id in END_OF_CHANNELS:
            reading.in_channels = False
        if reading.in_channels:
            _channel_event(reading, event.id, event.data, event.text)
        _song_event(reading, event.id, event.data, event.text)
    return Project(
        ppq=flp.ppq,
        channels=tuple(reading.names),
        automation=reading.automation,
        links={channel: tuple(ids) for channel, ids in reading.links.items()},
        arrangements=tuple(reading.arrangements),
        track_names=reading.track_names,
    )
