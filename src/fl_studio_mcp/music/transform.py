"""Note transformations. Every function returns new notes; inputs are never modified."""

from __future__ import annotations

import inspect
import math
import random
from dataclasses import replace
from typing import Any, Callable

from fl_studio_mcp.music.model import Note, Pitch, check_note_count, parse_pitch, pitch_class
from fl_studio_mcp.music.rhythm import swing
from fl_studio_mcp.music.theory import scale_intervals

ARPEGGIO_MODES = ("up", "down", "updown", "downup", "random")

# Starts equal to this many decimal places (of a beat) count as simultaneous.
ONSET_DECIMALS = 6


def transpose(notes: list[Note], semitones: int) -> list[Note]:
    return [replace(n, pitch=n.pitch + semitones) for n in notes]


def shift(notes: list[Note], beats: float) -> list[Note]:
    return [replace(n, start=n.start + beats) for n in notes]


def stretch(notes: list[Note], factor: float, anchor: float = 0.0) -> list[Note]:
    """Scale timing around `anchor` (factor 2 = half speed)."""
    if factor <= 0:
        raise ValueError(f"stretch factor must be positive, got {factor}")
    return [
        replace(n, start=anchor + (n.start - anchor) * factor, length=n.length * factor)
        for n in notes
    ]


def reverse(notes: list[Note]) -> list[Note]:
    """Mirror notes in time within the span they occupy."""
    if not notes:
        return []
    span_start = min(n.start for n in notes)
    span_end = max(n.end for n in notes)
    mirrored = [replace(n, start=span_start + span_end - n.end) for n in notes]
    return sorted(mirrored, key=lambda n: (n.start, n.pitch))


def quantize(notes: list[Note], grid: float, strength: float = 1.0) -> list[Note]:
    """Move note starts toward the nearest grid line (strength 0-1)."""
    if grid <= 0 or not 0 <= strength <= 1:
        raise ValueError("quantize needs a positive grid and strength 0-1")
    return [
        replace(n, start=n.start + (round(n.start / grid) * grid - n.start) * strength)
        for n in notes
    ]


def humanize(
    notes: list[Note], timing: float = 0.01, velocity: float = 0.05, seed: int = 0
) -> list[Note]:
    """Random timing (± beats) and velocity (±) variation; the same seed gives the same result."""
    rng = random.Random(seed)
    return [
        replace(
            n,
            start=max(0.0, n.start + rng.uniform(-timing, timing)),
            velocity=min(1.0, max(0.0, n.velocity + rng.uniform(-velocity, velocity))),
        )
        for n in notes
    ]


def snap_to_scale(notes: list[Note], root: str, scale: str) -> list[Note]:
    """Move out-of-scale notes to the nearest scale note (the lower one on a tie)."""
    root_pc = pitch_class(root)
    allowed = {(root_pc + i) % 12 for i in scale_intervals(scale)}

    def snap(pitch: int) -> int:
        for distance in range(12):
            for candidate in (pitch - distance, pitch + distance):
                if candidate % 12 in allowed and 0 <= candidate <= 127:
                    return candidate
        return pitch

    return [replace(n, pitch=snap(n.pitch)) for n in notes]


def _onset(start: float) -> float:
    """Grouping key for "the same time": tolerates float error (0.1 + 0.2 vs 0.3)."""
    return round(start, ONSET_DECIMALS)


def legato(notes: list[Note]) -> list[Note]:
    """Extend each note to the next onset; notes sharing a start move together."""
    onsets = sorted({_onset(n.start) for n in notes})
    next_onset = dict(zip(onsets, onsets[1:]))
    return [
        replace(n, length=next_onset[_onset(n.start)] - n.start)
        if _onset(n.start) in next_onset
        else n
        for n in notes
    ]


def set_length(notes: list[Note], length: float) -> list[Note]:
    return [replace(n, length=length) for n in notes]


def velocity_ramp(notes: list[Note], start: float, end: float) -> list[Note]:
    """Velocity changes linearly over time from `start` to `end`."""
    if not notes:
        return []
    first = min(n.start for n in notes)
    span = max(n.start for n in notes) - first
    return [
        replace(n, velocity=start + (end - start) * ((n.start - first) / span if span else 0))
        for n in notes
    ]


def repeat(notes: list[Note], times: int, every: float) -> list[Note]:
    """`times` copies of the notes, each `every` beats after the previous one."""
    if times < 1 or every <= 0:
        raise ValueError("repeat needs times >= 1 and a positive 'every'")
    check_note_count(len(notes) * times)
    return [replace(n, start=n.start + i * every) for i in range(times) for n in notes]


def _arpeggio_sequence(pitches: list[int], mode: str, octaves: int) -> list[int]:
    pool = [p + 12 * o for o in range(octaves) for p in sorted(pitches)]
    if mode in ("up", "random"):
        return pool
    if mode == "down":
        return pool[::-1]
    if mode == "updown":
        return pool + pool[-2:0:-1]
    return pool[::-1] + pool[1:-1]  # downup


def arpeggiate(
    notes: list[Note],
    step: float,
    mode: str = "up",
    octaves: int = 1,
    gate: float = 1.0,
    seed: int = 0,
) -> list[Note]:
    """Turn each group of simultaneous notes (a chord) into an arpeggio over its length.

    Args:
        step: Time between arpeggio notes in beats.
        mode: up, down, updown, downup or random.
        octaves: How many octaves the arpeggio climbs through.
        gate: Fraction of `step` each arpeggio note sounds (0-1].
    """
    if mode not in ARPEGGIO_MODES:
        raise ValueError(f"Unknown arpeggio mode '{mode}'. Use one of: {', '.join(ARPEGGIO_MODES)}")
    if step <= 0 or octaves < 1 or not 0 < gate <= 1:
        raise ValueError("arpeggiate needs step > 0, octaves >= 1 and gate in (0, 1]")

    rng = random.Random(seed)
    groups: dict[float, list[Note]] = {}
    for n in notes:
        groups.setdefault(_onset(n.start), []).append(n)

    def step_count(chord_notes: list[Note]) -> int:
        return max(1, round(max(n.length for n in chord_notes) / step))

    check_note_count(sum(step_count(g) for g in groups.values()))

    result = []
    for key in sorted(groups):
        chord_notes = groups[key]
        start = min(n.start for n in chord_notes)
        sequence = _arpeggio_sequence([n.pitch for n in chord_notes], mode, octaves)
        for k in range(step_count(chord_notes)):
            pitch = rng.choice(sequence) if mode == "random" else sequence[k % len(sequence)]
            result.append(replace(chord_notes[0], pitch=parse_pitch(pitch),
                                  start=start + k * step, length=step * gate))
    return result


STRUM_DIRECTIONS = ("up", "down", "alternate")
VELOCITY_SHAPES = ("linear", "exp", "log", "sine")


def _chords(notes: list[Note]) -> list[list[Note]]:
    """Groups of simultaneous notes in time order, each sorted low to high."""
    groups: dict[float, list[Note]] = {}
    for n in notes:
        groups.setdefault(_onset(n.start), []).append(n)
    return [sorted(groups[key], key=lambda n: n.pitch) for key in sorted(groups)]


def fix_overlaps(notes: list[Note]) -> list[Note]:
    """Same-pitch notes never overlap: a note ends where the next one of its pitch starts.

    Of notes stacked on the same pitch and start, the longest (then loudest) is kept.
    """
    best: dict[tuple[int, float], Note] = {}
    for n in notes:
        key = (n.pitch, _onset(n.start))
        if key not in best or (n.length, n.velocity) > (best[key].length, best[key].velocity):
            best[key] = n
    by_pitch = sorted(best.values(), key=lambda n: (n.pitch, n.start))
    fixed = [
        replace(n, length=nxt.start - n.start)
        if nxt.pitch == n.pitch and n.end > nxt.start
        else n
        for n, nxt in zip(by_pitch, by_pitch[1:])
    ] + by_pitch[-1:]
    return sorted(fixed, key=lambda n: (n.start, n.pitch))


def strum(notes: list[Note], spread: float = 0.02, direction: str = "up") -> list[Note]:
    """Each chord's voices start `spread` beats apart; every voice still ends where it did.

    Args:
        direction: up (lowest first), down, or alternate (up, down, up, ... chord by chord).
    """
    if direction not in STRUM_DIRECTIONS:
        raise ValueError(
            f"Unknown strum direction '{direction}'. Use: {', '.join(STRUM_DIRECTIONS)}"
        )
    if spread < 0:
        raise ValueError(f"strum spread must be 0 or more, got {spread}")
    result = []
    for index, chord in enumerate(_chords(notes)):
        down = direction == "down" or (direction == "alternate" and index % 2 == 1)
        voices = chord[::-1] if down else chord
        for k, n in enumerate(voices):
            offset = k * spread
            if k and offset >= n.length:
                raise ValueError(
                    f"A strum spread of {spread} beats delays voice {k + 1} past its end "
                    f"(length {n.length}); use a smaller spread"
                )
            result.append(replace(n, start=n.start + offset, length=n.length - offset))
    return result


def flam(notes: list[Note], offset: float = 0.03, velocity: float = 0.6) -> list[Note]:
    """Put a quieter grace note `offset` beats before every note (a drum flam).

    `velocity` is the grace note's share of the main note's velocity. Notes closer than
    `offset` to the pattern start get none.
    """
    if offset <= 0 or not 0 <= velocity <= 1:
        raise ValueError("flam needs a positive offset and velocity 0-1")
    starts = [n for n in notes if n.start - offset >= -1e-9]
    check_note_count(len(notes) + len(starts))
    graces = [
        replace(n, start=max(0.0, n.start - offset), length=offset,
                velocity=n.velocity * velocity)
        for n in starts
    ]
    return sorted(graces + list(notes), key=lambda n: (n.start, n.pitch))


def chop(
    notes: list[Note], size: float | None = None, parts: int | None = None, gate: float = 1.0
) -> list[Note]:
    """Cut every note into pieces of `size` beats, or into `parts` equal pieces.

    `gate` is the share of each piece that sounds (0-1]; a last piece may be shorter.
    """
    if (size is None) == (parts is None):
        raise ValueError("chop needs exactly one of 'size' or 'parts'")
    if (size is not None and size <= 0) or (parts is not None and parts < 1) or not 0 < gate <= 1:
        raise ValueError("chop needs size > 0 or parts >= 1, and gate in (0, 1]")

    def pieces(n: Note) -> list[tuple[float, float]]:
        if n.length <= 0:  # a step hit: nothing to cut
            return [(n.start, n.length)]
        step = size if size is not None else n.length / parts
        count = max(1, math.ceil(n.length / step - 1e-9))
        cut = [(n.start + k * step, min(step, n.end - (n.start + k * step))) for k in range(count)]
        return [(start, length) for start, length in cut if length > 1e-9]

    cut = [(n, pieces(n)) for n in notes]
    check_note_count(sum(len(p) for _, p in cut))
    return [
        replace(n, start=start, length=length * gate)
        for n, pieces_of_n in cut
        for start, length in pieces_of_n
    ]


def _curve(t: float, shape: str) -> float:
    if shape == "exp":
        return t * t
    if shape == "log":
        return 1 - (1 - t) ** 2
    if shape == "sine":
        return (1 - math.cos(math.pi * t)) / 2
    return t


def velocity_curve(
    notes: list[Note], start: float, end: float, shape: str = "linear"
) -> list[Note]:
    """Velocity runs from `start` to `end` over time along a curve.

    Args:
        shape: linear, exp (slow then fast, a build-up), log (fast then slow) or sine (S-curve).
    """
    if shape not in VELOCITY_SHAPES:
        raise ValueError(f"Unknown velocity shape '{shape}'. Use: {', '.join(VELOCITY_SHAPES)}")
    if not notes:
        return []
    first = min(n.start for n in notes)
    span = max(n.start for n in notes) - first

    def position(n: Note) -> float:
        return (n.start - first) / span if span else 0.0

    return [replace(n, velocity=start + (end - start) * _curve(position(n), shape)) for n in notes]


def accent(notes: list[Note], pattern: list[float], every: float = 0.25) -> list[Note]:
    """Scale velocities by a repeating accent pattern laid on a grid of `every` beats.

    E.g. pattern [1, 0.6, 0.8, 0.6] with every 0.25 accents each beat's 16ths. Results are
    clamped to 0-1.
    """
    if not pattern or every <= 0 or any(f < 0 for f in pattern):
        raise ValueError("accent needs a non-empty pattern of factors >= 0 and a positive 'every'")
    return [
        replace(n, velocity=min(1.0, n.velocity * pattern[round(n.start / every) % len(pattern)]))
        for n in notes
    ]


def groove(notes: list[Note], template: list[dict], step: float = 0.25) -> list[Note]:
    """Give notes a groove: each grid step's timing offset and velocity factor.

    `template` holds one {"offset" (beats), "velocity" (factor)} per step of a cycle,
    e.g. what audio_drums reads from a loop; a note takes the entry of the step it
    lies nearest. Velocities are clamped to 0-1.
    """
    if not template or step <= 0:
        raise ValueError("groove needs a non-empty template and a positive step")
    result = []
    for n in notes:
        entry = template[round(n.start / step) % len(template)]
        offset = float(entry.get("offset", 0.0))
        factor = float(entry.get("velocity", 1.0))
        result.append(replace(n, start=max(0.0, n.start + offset),
                              velocity=min(1.0, max(0.0, n.velocity * factor))))
    return result


def invert(notes: list[Note], axis: Pitch = 60) -> list[Note]:
    """Mirror pitches around `axis` (a melodic inversion: up a third becomes down a third)."""
    center = parse_pitch(axis)
    return [replace(n, pitch=parse_pitch(2 * center - n.pitch)) for n in notes]


OPERATIONS: dict[str, Callable[..., list[Note]]] = {
    "transpose": transpose,
    "shift": shift,
    "stretch": stretch,
    "reverse": reverse,
    "quantize": quantize,
    "swing": swing,
    "humanize": humanize,
    "snap_to_scale": snap_to_scale,
    "legato": legato,
    "set_length": set_length,
    "velocity_ramp": velocity_ramp,
    "repeat": repeat,
    "arpeggiate": arpeggiate,
    "fix_overlaps": fix_overlaps,
    "strum": strum,
    "flam": flam,
    "chop": chop,
    "velocity_curve": velocity_curve,
    "accent": accent,
    "invert": invert,
    "groove": groove,
}


def describe_operations() -> dict[str, list[str]]:
    """Operation names and their parameters (for tool documentation)."""
    return {
        name: [p for p in inspect.signature(fn).parameters if p != "notes"]
        for name, fn in OPERATIONS.items()
    }


def apply_operations(notes: list[Note], operations: list[dict[str, Any]]) -> list[Note]:
    """Apply operations in order, e.g. [{"op": "transpose", "semitones": 12}]."""
    result = list(notes)
    for operation in operations:
        params = dict(operation)
        name = params.pop("op", None)
        if name is None:
            raise ValueError(f"Each operation needs an 'op' key, got {operation!r}")
        if name not in OPERATIONS:
            raise ValueError(f"Unknown operation '{name}'. Available: {', '.join(OPERATIONS)}")
        fn = OPERATIONS[name]
        try:
            inspect.signature(fn).bind(result, **params)
        except TypeError as e:
            expected = ", ".join(describe_operations()[name]) or "none"
            raise ValueError(f"Invalid parameters for '{name}': {e}. Expected: {expected}") from e
        result = fn(result, **params)
    return result
