"""Step grids, Euclidean rhythms, rolls and swing.

Grid syntax: "x" hit, "X" accented hit, "_" extends the previous hit (tie),
"." or "-" rest. Spaces and "|" are ignored, so bars can be separated visually.
"E(pulses,steps)" or "E(pulses,steps,rotation)" expands to a Euclidean rhythm.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

from fl_studio_mcp.music.model import (
    DEFAULT_VELOCITY,
    Note,
    Pitch,
    check_note_count,
    parse_pitch,
)

HIT = "x"
ACCENT = "X"
TIE = "_"
RESTS = ".-"
IGNORED = " |"

_EUCLID = re.compile(r"E\(\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(-?\d+)\s*)?\)")
_EPSILON = 1e-9


@dataclass(frozen=True)
class Hit:
    step: int
    length: int  # in steps, including ties
    accent: bool


def euclid(pulses: int, steps: int, rotation: int = 0) -> str:
    """Spread `pulses` hits as evenly as possible over `steps` ("x..x..x." for 3, 8)."""
    if steps < 1 or not 0 <= pulses <= steps:
        raise ValueError(f"Euclidean rhythm needs 0 <= pulses <= steps, got E({pulses},{steps})")
    pattern = "".join(HIT if (i * pulses) % steps < pulses else "." for i in range(steps))
    shift = rotation % steps
    return pattern[shift:] + pattern[:shift]


def expand_pattern(pattern: str) -> str:
    """Expand E(...) terms and drop spaces and bar lines."""
    expanded = _EUCLID.sub(
        lambda m: euclid(int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)), pattern
    )
    return "".join(c for c in expanded if c not in IGNORED)


def parse_grid(pattern: str) -> tuple[list[Hit], int]:
    """Hits in a grid pattern and its total number of steps."""
    grid = expand_pattern(pattern)
    hits: list[Hit] = []
    tying = False

    for step, symbol in enumerate(grid):
        if symbol in (HIT, ACCENT):
            hits.append(Hit(step, 1, symbol == ACCENT))
            tying = True
        elif symbol == TIE:
            if tying:
                hits[-1] = replace(hits[-1], length=hits[-1].length + 1)
        elif symbol in RESTS:
            tying = False
        else:
            raise ValueError(
                f"Unknown grid symbol {symbol!r} in {pattern!r}. "
                "Use x (hit), X (accent), _ (tie), . or - (rest)"
            )

    return hits, len(grid)


def _as_pitches(pitches: Pitch | list[Pitch]) -> list[int]:
    values = pitches if isinstance(pitches, list) else [pitches]
    if not values:
        raise ValueError("At least one pitch is required")
    return [parse_pitch(p) for p in values]


def rhythm_notes(
    pattern: str,
    pitches: Pitch | list[Pitch],
    step: float = 0.25,
    start: float = 0.0,
    velocity: float = DEFAULT_VELOCITY,
    accent_velocity: float = 1.0,
    gate: float = 1.0,
    repeat: int = 1,
) -> list[Note]:
    """Notes for a grid pattern; every hit plays all `pitches` (a chord for several).

    Args:
        step: Length of one grid step in beats (0.25 = sixteenth notes).
        gate: Fraction of each hit's length that sounds (0-1].
        repeat: How many times to play the pattern back to back.
    """
    if step <= 0:
        raise ValueError(f"step must be positive, got {step}")
    if not 0 < gate <= 1:
        raise ValueError(f"gate must be in (0, 1], got {gate}")
    if repeat < 1:
        raise ValueError(f"repeat must be >= 1, got {repeat}")

    midi = _as_pitches(pitches)
    hits, steps = parse_grid(pattern)
    check_note_count(len(hits) * len(midi) * repeat)
    return [
        Note(
            pitch=p,
            start=start + (r * steps + hit.step) * step,
            length=hit.length * step * gate,
            velocity=accent_velocity if hit.accent else velocity,
        )
        for r in range(repeat)
        for hit in hits
        for p in midi
    ]


def roll(
    start: float,
    length: float,
    rates: list[int],
    pitch: Pitch,
    velocity_start: float = DEFAULT_VELOCITY,
    velocity_end: float | None = None,
) -> list[Note]:
    """A roll that speeds up: the span is split evenly between `rates` (hits per beat).

    rates=[1, 2, 4, 8] over 4 beats gives quarters, eighths, sixteenths, then 32nds.
    Velocity ramps linearly from velocity_start to velocity_end (default: constant).
    """
    if length <= 0 or not rates or any(r <= 0 for r in rates):
        raise ValueError("roll needs a positive length and positive rates")

    midi = parse_pitch(pitch)
    segment = length / len(rates)
    check_note_count(sum(round(segment * rate) for rate in rates))
    onsets = [
        (start + i * segment + k / rate, 1 / rate)
        for i, rate in enumerate(rates)
        for k in range(round(segment * rate))
    ]
    end_velocity = velocity_start if velocity_end is None else velocity_end
    last = max(len(onsets) - 1, 1)
    return [
        Note(midi, onset, note_length,
             velocity=velocity_start + (end_velocity - velocity_start) * k / last)
        for k, (onset, note_length) in enumerate(onsets)
    ]


def swing(notes: list[Note], amount: float, grid: float = 0.5) -> list[Note]:
    """Delay notes on every second `grid` position; amount 1.0 = triplet feel."""
    if not 0 <= amount <= 1:
        raise ValueError(f"swing amount must be 0-1, got {amount}")
    if grid <= 0:
        raise ValueError(f"grid must be positive, got {grid}")

    delay = amount * grid / 3

    def is_off_beat(start: float) -> bool:
        position = start / grid
        return abs(position - round(position)) < _EPSILON and round(position) % 2 == 1

    return [replace(n, start=n.start + delay) if is_off_beat(n.start) else n for n in notes]
