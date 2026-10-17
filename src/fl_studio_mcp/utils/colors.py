"""Colours by name or hex code, as FL's API takes them: 0xRRGGBB."""

from __future__ import annotations

import re

NAMED_COLORS: dict[str, int] = {
    "black": 0x000000,
    "white": 0xFFFFFF,
    "grey": 0x808080,
    "gray": 0x808080,
    "red": 0xE53935,
    "dark red": 0x8E1B1B,
    "orange": 0xFB8C00,
    "amber": 0xFFB300,
    "yellow": 0xFDD835,
    "lime": 0xC0CA33,
    "green": 0x43A047,
    "dark green": 0x1B5E20,
    "teal": 0x00897B,
    "cyan": 0x00ACC1,
    "light blue": 0x4FC3F7,
    "blue": 0x1E88E5,
    "dark blue": 0x1A237E,
    "indigo": 0x3949AB,
    "purple": 0x8E24AA,
    "violet": 0x7E57C2,
    "magenta": 0xD81B60,
    "pink": 0xF06292,
    "brown": 0x6D4C41,
}

_HEX = re.compile(r"^(?:#|0x)?([0-9a-f]{6}|[0-9a-f]{3})$")


def parse_color(spec: str) -> int:
    """0xRRGGBB for a colour name ("light blue"), "#ff8800", "ff8800", "#f80" or "0xff8800".

    Raises ValueError for anything else.
    """
    text = " ".join(str(spec).lower().replace("_", " ").split())
    if text in NAMED_COLORS:
        return NAMED_COLORS[text]
    match = _HEX.match(text.replace(" ", ""))
    if not match:
        names = ", ".join(sorted(NAMED_COLORS))
        raise ValueError(f"Unknown colour {spec!r}: use a hex code like #ff8800 or one of {names}")
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(d * 2 for d in digits)
    return int(digits, 16)


def rgb(color: int) -> tuple[int, int, int]:
    """The red, green and blue components of 0xRRGGBB."""
    return (color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF
