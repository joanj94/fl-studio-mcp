"""Automation clip presets (.fst) with points of our own.

FL ships automation clips as channel presets (Data/Patches/Channel presets/
Automation clips). Loaded into an automation clip's channel ("Send to selected
channel"), such a preset replaces the clip's points and keeps what the clip is
linked to (seen live, FL 2026). So a clip FL made for a parameter gets any
shape by writing a preset and loading it.

The events below are those of FL's stock "Special/60-180 Tempo.fst", with its
points, name and range (event 219, now the parameter's whole range) replaced.
Points are in beats from the clip's start (each stored as the distance from the
point before), values 0-1 over the parameter's whole range, tension -1..1 (0 is
a straight line). The clip
FL shows is as long as its last point.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from fl_studio_mcp.project.flp import Event, FlpFile, build

PRESET_FORMAT = 0x20
PRESET_CHANNELS = 5
PRESET_PPQ = 96
MAX_POINTS = 2000
MAX_NAME = 100


def _e(event_id: int, value: int | str) -> Event:
    """An event from a number (for fixed sizes) or hex text (for data)."""
    if isinstance(value, str):
        return Event(event_id, bytes.fromhex(value))
    size = 1 if event_id < 64 else 2 if event_id < 128 else 4
    return Event(event_id, value.to_bytes(size, "little"))


_BEFORE_POINTS = (
    _e(199, "392e302e3000"),  # the FL version that wrote it: "9.0.0"
    _e(28, 3), _e(64, 4), _e(21, 5),  # 21: channel type 5, an automation clip
    _e(0, 1),
    _e(209, "0000000000190000000000000400000090000000"),
    _e(138, 8388736), _e(139, 65536), _e(89, 0), _e(69, 128), _e(86, 256), _e(71, 1024),
    _e(83, 0), _e(74, 0), _e(75, 0), _e(76, 0), _e(85, 2048), _e(131, 8388608), _e(70, 0),
    _e(22, 0),
    # 219: the range the clip's 0-1 covers, min and max of 12800. The stock preset's
    # 647-2200 (its 60-180 BPM) put a point at 0.8 on a fader at 0.148 (seen live);
    # a clip FL makes itself has 0-12800.
    _e(219, "000000000032000000000000000100000000000000000000"),
    _e(229, "0000000000320000000000000000000000000000"),
    _e(221, "01000000f401000000"),
    _e(215, "ffffffff0000000001000000ffffffff3c0000000000803f0000803f0000803f0000803f"
            "0000803f0000000001000000ffffffff000400003000000000000100a705000000000000"
            "0001000000000000000000000000000000000000010000000000000000000000000000"
            "0000000000"),
    _e(132, 0), _e(144, 0), _e(145, 0),
)
_POINTS_ID = 234
_POINTS_HEAD = bytes.fromhex("0100000040000000000400000002000000")
_POINTS_TAIL = bytes.fromhex(
    "01000000ffffffffffffffffffffffffffffffff800000008000000000000000800000000500"
    "000002000000010000000000000000000000000000000000f03f000000000000000001000000"
    "00000000fffffffffffffffffffffffffbb20000000000000000000000000000"
)
_LEVELS = ("000000000000000064000000204e0000204e00003075000032000000204e00000000000064"
           "000000204e000000000000b680000000000000000000000000000000000000")
_AFTER_POINTS = (
    _e(228, "64000000000000000000000000000000"),
    _e(228, "3c000000000000000000000000000000"),
    _e(218, _LEVELS),
    _e(218, "04" + _LEVELS[2:-8] + "9bffffff"),
    _e(218, _LEVELS), _e(218, _LEVELS), _e(218, _LEVELS),
    _e(143, 3), _e(20, 0),
)
_NAME_ID = 192
_AFTER_NAME = (_e(155, 0), _e(128, 6321038))  # 128: the channel's colour


@dataclass(frozen=True)
class Point:
    beat: float  # from the clip's start
    value: float  # 0-1
    tension: float = 0.0


def check_points(points: list[Point]) -> None:
    """Raise ValueError unless the points make a clip FL can show."""
    if len(points) < 2:
        raise ValueError("An automation clip needs at least two points.")
    if len(points) > MAX_POINTS:
        raise ValueError(f"At most {MAX_POINTS} points.")
    if points[0].beat != 0:
        raise ValueError("The first point must be at the clip's start (beat 0).")
    for before, after in zip(points, points[1:]):
        if after.beat < before.beat:
            raise ValueError("Points must be in time order.")
    if points[-1].beat <= 0:
        raise ValueError("The last point must lie after the first.")
    for point in points:
        if not 0.0 <= point.value <= 1.0:
            raise ValueError(f"Point values run from 0 to 1, not {point.value}.")
        if not -1.0 <= point.tension <= 1.0:
            raise ValueError(f"Tension runs from -1 to 1, not {point.tension}.")


def points_data(points: list[Point]) -> bytes:
    """The data of the event 234 holding these points."""
    body, before = bytearray(), 0.0
    for point in points:
        body += struct.pack("<ddf", point.beat - before, point.value, point.tension) + bytes(4)
        before = point.beat
    return _POINTS_HEAD + struct.pack("<I", len(points)) + bytes(body) + _POINTS_TAIL


def automation_preset(points: list[Point], name: str) -> bytes:
    """The bytes of an automation clip preset with these points; raises ValueError."""
    check_points(points)
    label = name.strip()[:MAX_NAME] or "Automation"
    events = (
        *_BEFORE_POINTS,
        Event(_POINTS_ID, points_data(points)),
        *_AFTER_POINTS,
        Event(_NAME_ID, label.encode("latin-1", "replace") + b"\0"),
        *_AFTER_NAME,
    )
    return build(FlpFile(PRESET_FORMAT, PRESET_CHANNELS, PRESET_PPQ, events))
