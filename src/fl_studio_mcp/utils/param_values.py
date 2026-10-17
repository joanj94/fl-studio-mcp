"""Plugin parameters set by what they display ("250Hz", "-6 dB", "1.5 s", "4:1", "Peaking").

FL's API sets parameters as 0-1 and shows each as text (getParamValueString),
whose scale differs per parameter (logarithmic frequencies, curved times). So a
value is found by trying positions and reading the text back: a bisection for
numbers, a scan for named settings (filter types, modes).

Seen live (FL 2026): right after setParamValue, some plugins (Fruity Limiter,
Compressor, Reeverb 2, Delay 3) still report the old value, and Delay 3 the old
text too; a separate command reads the new one. So `read_at` must set and then
read in two commands.
"""

from __future__ import annotations

import math
import re
from typing import Callable

# unit -> (kind, factor to the kind's base unit)
UNITS: dict[str, tuple[str, float]] = {
    "hz": ("frequency", 1.0),
    "khz": ("frequency", 1000.0),
    "k": ("frequency", 1000.0),
    "ms": ("time", 0.001),
    "s": ("time", 1.0),
    "sec": ("time", 1.0),
    "db": ("level", 1.0),
    "%": ("percent", 1.0),
    "": ("number", 1.0),
}
BISECT_STEPS = 22
SCAN_POINTS = 64
# A target this share of the range beyond an end still counts as that end.
RANGE_TOLERANCE = 0.01

_COLON = re.compile(r"^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$")
_NUMBER = re.compile(r"^([-+]?\d+(?:\.\d+)?)([a-z%]*)$")

ReadAt = Callable[[float], str]


def parse_display(text: str, colon: str = "ratio") -> tuple[str, float] | None:
    """(kind, number in the kind's base unit) for a display text, or None for a name.

    `colon` says how "a:b" reads: "ratio" (a/b, a compressor's "4.0:1") or
    "position" (a then b, a synced delay time "4:1" = 4 steps and 1 tick).
    """
    compact = str(text).strip().lower().replace(" ", "")
    if compact in ("-infdb", "-inf"):
        return "level", -math.inf
    match = _COLON.match(compact)
    if match:
        a, b = float(match.group(1)), float(match.group(2))
        if colon == "position":
            return "position", a + b / 1000
        return "ratio", a / b if b else math.inf
    match = _NUMBER.match(compact)
    if match and match.group(2) in UNITS:
        kind, factor = UNITS[match.group(2)]
        return kind, float(match.group(1)) * factor
    return None


def _colon_style(texts: tuple[str, ...]) -> str:
    """How this parameter's "a:b" texts read: decimals are ratios, whole numbers positions."""
    colon = [t for t in texts if _COLON.match(str(t).strip().replace(" ", ""))]
    return "position" if colon and not any("." in t for t in colon) else "ratio"


def _same(a: str, b: str) -> bool:
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


def find_value(target: str, read_at: ReadAt) -> tuple[float, str]:
    """The 0-1 value whose display is (closest to) `target`, and its display; ends set there.

    `read_at(v)` sets the parameter to v and returns its display text. Raises
    ValueError when the target is out of range, has the wrong unit or names no
    setting.
    """
    ends = (read_at(0.0), read_at(1.0))
    style = _colon_style(ends)
    wanted = parse_display(target, style)
    if wanted is None:
        return _scan(target, read_at)
    # An end may show a name ("Off" at a filter's top): step in to where numbers start.
    span = []
    for start, step in ((0.0, 1 / SCAN_POINTS), (1.0, -1 / SCAN_POINTS)):
        position, text = start, ends[0] if start == 0.0 else ends[1]
        while parse_display(text, style) is None and 0.0 < position + step < 1.0 \
                and abs(position - start) < 0.25:
            position += step
            text = read_at(position)
        span.append((position, text, parse_display(text, style)))
    (lo_pos, lo_text, low), (hi_pos, hi_text, high) = span
    if low is None or high is None or low[0] != high[0] or wanted[0] != low[0]:
        raise ValueError(
            f"{target!r} doesn't read like this parameter's values "
            f"({ends[0]!r} to {ends[1]!r}): give it in the same unit, or a setting's name"
        )
    return _bisect(wanted[1], ((lo_pos, low[1]), (hi_pos, high[1])), (lo_text, hi_text),
                   style, read_at)


def _bisect(wanted: float, bounds: tuple[tuple[float, float], tuple[float, float]],
            ends: tuple[str, str], style: str, read_at: ReadAt) -> tuple[float, str]:
    (lo, low), (hi, high) = bounds
    finite = [n for n in (low, high) if math.isfinite(n)]
    slack = abs(finite[-1] - finite[0]) * RANGE_TOLERANCE if len(finite) == 2 else 0.0
    if not min(low, high) - slack <= wanted <= max(low, high) + slack:
        raise ValueError(
            f"{wanted:g} is outside this parameter's range, {ends[0]!r} to {ends[1]!r}"
        )
    rising = high > low
    candidates = [(abs(low - wanted), lo), (abs(high - wanted), hi)]
    for _ in range(BISECT_STEPS):
        mid = (lo + hi) / 2
        text = read_at(mid)
        reading = parse_display(text, style)
        if reading is None:
            raise ValueError(f"The parameter showed {text!r}, which isn't a number")
        candidates.append((abs(reading[1] - wanted), mid))
        if (reading[1] < wanted) == rising:
            lo = mid
        else:
            hi = mid
    value = min(candidates)[1]
    return value, read_at(value)


def _scan(target: str, read_at: ReadAt) -> tuple[float, str]:
    """Named settings: try evenly spaced positions, coarse first, and take the middle of
    the first matching run (a switch or a type has few settings: 17 positions find them)."""
    for points in (SCAN_POINTS // 4, SCAN_POINTS):
        positions = [i / points for i in range(points + 1)]
        texts = [read_at(v) for v in positions]
        hits = [i for i, text in enumerate(texts) if _same(text, target)]
        if hits:
            break
    else:
        seen = list(dict.fromkeys(texts))
        raise ValueError(f"No setting shows {target!r}; this parameter shows {seen}")
    run = [hits[0]]
    for i in hits[1:]:
        if i != run[-1] + 1:
            break
        run.append(i)
    value = positions[run[len(run) // 2]]
    return value, read_at(value)
