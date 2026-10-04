"""Laying sections out in time and filling them with a pattern's clips or its notes.

Notes here are plain dicts in fl_send_notes format ("time" and "duration" in
beats), so every other field a note carries survives unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil


@dataclass(frozen=True)
class Section:
    """A stretch of the arrangement in which some patterns play, looped."""

    name: str
    start: float  # beats from the start of the arrangement
    length: float  # beats
    patterns: tuple[int, ...]
    # Patterns played every so many bars instead of back to back (a crash on
    # the first beat of every 8 bars), by pattern index.
    every_bars: dict[int, int] = field(default_factory=dict)
    # The section's bar (1-based) the first of those starts on, if not its first.
    from_bar: dict[int, int] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.length


def _is_whole_number(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pattern_entry(entry: object, number: int) -> tuple[int, tuple[int, int] | None]:
    """(pattern index, (every so many bars, from which bar) or None) of one entry of a
    section's patterns."""
    if _is_whole_number(entry) and entry >= 1:
        return entry, None
    if isinstance(entry, dict) and set(entry) in ({"pattern", "every_bars"},
                                                  {"pattern", "every_bars", "from_bar"}):
        values = entry["pattern"], entry["every_bars"], entry.get("from_bar", 1)
        if all(_is_whole_number(v) and v >= 1 for v in values):
            return values[0], values[1:]
    raise ValueError(
        f"Section {number}: 'patterns' must be a list of pattern indexes, or of "
        '{"pattern": index, "every_bars": bars, "from_bar"?: bar} for a pattern played '
        "every so many bars"
    )


def layout_sections(sections: list[dict], beats_per_bar: int) -> list[Section]:
    """Place sections back to back. Each is {"bars", "patterns", "name"?}.

    "patterns" may be empty: that section is silence. An entry is a pattern
    index, or {"pattern", "every_bars", "from_bar"?} for a pattern that is not
    repeated back to back but started every so many bars, from the section's
    first bar or from its bar "from_bar". Raises ValueError on bad input.
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
        if not isinstance(patterns, list):
            raise ValueError(f"Section {number}: 'patterns' must be a list of pattern indexes")
        entries = dict(_pattern_entry(entry, number) for entry in patterns)
        length = float(bars * beats_per_bar)
        name = str(section.get("name") or f"section {number}")
        timed = {pattern: gap for pattern, gap in entries.items() if gap is not None}
        laid_out.append(Section(name, start, length, tuple(entries),
                                {pattern: every for pattern, (every, _) in timed.items()},
                                {pattern: bar for pattern, (_, bar) in timed.items() if bar > 1}))
        start += length
    return laid_out


def clip_bars(
    sections: list[Section], lengths: dict[int, float], beats_per_bar: int
) -> dict[int, list[int]]:
    """The bars (1-based) at which each pattern's clips start, to fill its sections.

    A clip is as long as its pattern and can't be cut, so a section must be a
    whole number of repeats of each of its patterns; a pattern with
    "every_bars" starts every so many bars instead (from the section's bar
    "from_bar", if given) and only has to fit once.
    `lengths` are the patterns' lengths in beats. Raises ValueError for a
    pattern that is empty or doesn't fit.
    """
    bars: dict[int, list[int]] = {}
    for section in sections:
        for pattern in section.patterns:
            if not lengths.get(pattern):
                raise ValueError(f"Pattern {pattern} is empty, so it can't be arranged")
            step = ceil(lengths[pattern] / beats_per_bar)
            section_bars = int(section.length // beats_per_bar)
            first = int(section.start // beats_per_bar) + 1
            every = section.every_bars.get(pattern)
            if every is not None:
                if every < step:
                    raise ValueError(
                        f"Pattern {pattern} is {step} bars long, so it can't start every "
                        f"{every} bars (section '{section.name}')"
                    )
                starts = range(first + section.from_bar.get(pattern, 1) - 1,
                               first + section_bars - step + 1, every)
                if not starts:
                    raise ValueError(
                        f"Pattern {pattern} is {step} bars long and doesn't fit section "
                        f"'{section.name}' ({section_bars} bars)"
                    )
                bars.setdefault(pattern, []).extend(starts)
                continue
            if section_bars % step:
                raise ValueError(
                    f"Pattern {pattern} is {step} bars long and doesn't fit section "
                    f"'{section.name}' ({section_bars} bars): a section must be a whole number "
                    "of repeats of each of its patterns. Change the section's length, or "
                    "make a shorter pattern for it."
                )
            bars.setdefault(pattern, []).extend(range(first, first + section_bars, step))
    return bars


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
        for key in ("time", "duration"):
            value = note.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"A note's '{key}' must be a number, got {value!r}")
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
