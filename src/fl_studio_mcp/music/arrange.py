"""Laying sections out in time and looping a pattern's notes to fill them.

Notes here are plain dicts in fl_send_notes format ("time" and "duration" in
beats), so every other field a note carries survives unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil


@dataclass(frozen=True)
class Section:
    """A stretch of the arrangement in which some patterns play, looped."""

    name: str
    start: float  # beats from the start of the arrangement
    length: float  # beats
    patterns: tuple[int, ...]

    @property
    def end(self) -> float:
        return self.start + self.length


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def layout_sections(sections: list[dict], beats_per_bar: int) -> list[Section]:
    """Place sections back to back. Each is {"bars", "patterns", "name"?}.

    "patterns" may be empty: that section is silence. Raises ValueError on bad input.
    """
    if not sections:
        raise ValueError("At least one section is needed")
    laid_out = []
    start = 0.0
    for number, section in enumerate(sections, start=1):
        if not isinstance(section, dict):
            raise ValueError(f"Section {number} must be an object with 'bars' and 'patterns'")
        bars = section.get("bars")
        if not _is_whole_number(bars) or bars < 1:
            raise ValueError(f"Section {number}: 'bars' must be a whole number of 1 or more")
        patterns = section.get("patterns", [])
        if not isinstance(patterns, list) or not all(
            _is_whole_number(p) and p >= 1 for p in patterns
        ):
            raise ValueError(f"Section {number}: 'patterns' must be a list of pattern indexes")
        length = float(bars * beats_per_bar)
        name = str(section.get("name") or f"section {number}")
        laid_out.append(Section(name, start, length, tuple(dict.fromkeys(patterns))))
        start += length
    return laid_out


def tile_notes(notes: list[dict], source_length: float, start: float, length: float) -> list[dict]:
    """Copies of `notes`, looped every `source_length` beats, filling [start, start + length).

    Notes are cut where the loop restarts and at the end of the span; a duration
    of 0 (a step sequencer hit) stays 0. Notes starting at or after
    `source_length` are outside the loop and dropped. Raises ValueError for a
    note whose time or duration isn't a number.
    """
    if source_length <= 0:
        raise ValueError(f"A pattern must be longer than 0 beats to be looped, got {source_length}")
    for note in notes:
        for field in ("time", "duration"):
            value = note.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"A note's '{field}' must be a number, got {value!r}")
    end = start + length
    tiled = []
    for repeat in range(ceil(length / source_length)):
        offset = start + repeat * source_length
        for note in notes:
            time = note.get("time", 0)
            if not 0 <= time < source_length or offset + time >= end:
                continue
            duration = min(note.get("duration", 0), source_length - time, end - (offset + time))
            tiled.append({**note, "time": offset + time, "duration": duration})
    return tiled
