"""Tempo and key read from a sample's file name ("Loop_128bpm_F#m.wav", "Bass 140 BPM A minor").

Sample packs put them in the name; the sound itself isn't opened. A tempo needs
"bpm" next to the number (a bare number is as often a sample's index: "040 F").
A key is a capital letter A-G as a word of its own, with # or b ("sharp"/"flat",
also as the next word) and an optional mode ("m", "min", "minor", "maj", "major",
also as the next word). A key without a mode matches either mode and is a guess
(shown "F?"); a lone letter only counts at the end of the name or before a number
or tempo ("040 F", "F 140bpm"), not in words ("Drop A Beat") or after a word that
names variants ("Model D", "Vowel A", seen in FL's packs).
"""

from __future__ import annotations

import re

from fl_studio_mcp.music.model import pitch_class

MIN_BPM = 40.0
MAX_BPM = 300.0
_BPM = re.compile(
    r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*[-_ ]?\s*bpm|bpm\s*[-_ ]?\s*(\d{2,3}(?:\.\d+)?)",
    re.IGNORECASE)
_KEY = re.compile(r"^([A-G])(#|b|sharp|flat)?(m|min|minor|maj|major)?$")
_MODE_WORDS = {"m": "minor", "min": "minor", "minor": "minor",
               "maj": "major", "major": "major"}
_ACCIDENTALS = {"sharp": "#", "flat": "b"}
# Words a lone letter after names a variant, not a key (seen in FL's own packs).
_VARIANT_WORDS = {"model", "vowel", "type", "version", "ver", "var", "variation", "mic",
                  "take", "room", "part", "section", "side", "bank", "group", "kit", "set",
                  "mix", "layer", "zone", "row", "column", "option", "letter"}
_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _stem(path: str) -> str:
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def parse_bpm(name: str) -> float | None:
    for match in _BPM.finditer(name):
        value = float(match.group(1) or match.group(2))
        if MIN_BPM <= value <= MAX_BPM:
            return value
    return None


def _is_tag_word(word: str) -> bool:
    """A word that may follow a bare key letter: a number or a tempo ("140bpm", "BPM")."""
    return word[0].isdigit() or word.lower().startswith("bpm")


def _key_at(words: list[str], index: int) -> tuple[int, str | None] | None:
    match = _KEY.match(words[index])
    if not match:
        return None
    letter, accidental, mode = match.groups()
    after = words[index + 1:]
    if not accidental and after and after[0].lower() in _ACCIDENTALS:
        accidental, after = after[0].lower(), after[1:]
    if mode is None and after and after[0].lower() in _MODE_WORDS and len(after[0]) > 1:
        mode, after = after[0].lower(), after[1:]
    if not (accidental or mode):
        # A lone capital letter is as often a word ("Drop A Beat") or a variant
        # ("Model D"): it counts at the end of the name or before a number or tempo.
        if after and not _is_tag_word(after[0]):
            return None
        if index and words[index - 1].lower() in _VARIANT_WORDS:
            return None
    if words[index] == "Am" and index and words[index - 1] == "I":
        return None  # "I Am ..."
    accidental = _ACCIDENTALS.get(accidental, accidental or "")
    return pitch_class(letter + accidental), _MODE_WORDS.get(mode) if mode else None


def parse_key(name: str) -> tuple[int, str | None] | None:
    """(pitch class, "major"/"minor"/None) named in the name, or None.

    A key with a mode wins over one without; among equals the last one.
    """
    words = [w for w in re.split(r"[^A-Za-z0-9#]+", name) if w]
    found = [key for key in (_key_at(words, i) for i in range(len(words))) if key]
    with_mode = [key for key in found if key[1]]
    return (with_mode or found or [None])[-1]


def key_name(key: tuple[int, str | None]) -> str:
    """"F#m", "C", or "F?" for a key whose mode the name doesn't say."""
    pc, mode = key
    return _NAMES[pc] + {"minor": "m", "major": "", None: "?"}[mode]


def tags(path: str) -> dict:
    """{"bpm", "key"} of a browser path's file name (each None when the name has none)."""
    key = parse_key(_stem(path))
    return {"bpm": parse_bpm(_stem(path)), "key": key_name(key) if key else None}


_WANTED = re.compile(r"[A-G]\s*(#|b|\s+sharp|\s+flat|sharp|flat)?"
                     r"\s*(m|min|minor|maj|major)?", re.IGNORECASE)


def _wanted_keys(key: str, relative: bool) -> set[tuple[int, str]]:
    text = key.strip()
    text = text[:1].upper() + text[1:]
    wanted = parse_key(text) if _WANTED.fullmatch(text) else None
    if wanted is None:
        raise ValueError(f"Can't read the key {key!r}: write it like 'Am', 'F#m', 'Eb', "
                         "'A minor', 'Db major'")
    pc, mode = wanted[0], wanted[1] or "major"
    keys = {(pc, mode)}
    if relative:
        keys.add(((pc + 3) % 12, "major") if mode == "minor" else ((pc - 3) % 12, "minor"))
    return keys


def matcher(bpm: float | None = None, key: str | None = None, bpm_tolerance: float = 1.0,
            relative: bool = False):
    """A predicate on browser paths for the given tempo (± tolerance) and key, or None.

    `key` is a name like "F#m", "Ebm", "C"; with `relative` the relative major or
    minor matches too (Am finds C). Raises ValueError for a key it can't read.
    """
    if bpm is None and not key:
        return None
    if bpm_tolerance < 0:
        raise ValueError("bpm_tolerance can't be negative")
    keys = _wanted_keys(key, relative) if key else set()

    def keep(path: str) -> bool:
        stem = _stem(path)
        if bpm is not None:
            found = parse_bpm(stem)
            if found is None or abs(found - bpm) > bpm_tolerance:
                return False
        if keys:
            found_key = parse_key(stem)
            if found_key is None:
                return False
            pc, mode = found_key
            return any(pc == k_pc and mode in (None, k_mode) for k_pc, k_mode in keys)
        return True

    return keep
