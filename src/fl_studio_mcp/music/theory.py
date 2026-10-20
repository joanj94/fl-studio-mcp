"""Scales, chord symbols and roman numeral progressions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fl_studio_mcp.music.model import (
    FLAT_NAMES,
    MIDI_MAX,
    MIDI_MIN,
    SHARP_NAMES,
    Note,
    pitch_class,
)

SCALES: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
    "phrygian_dominant": (0, 1, 4, 5, 7, 8, 10),
    "hungarian_minor": (0, 2, 3, 6, 7, 8, 11),
    "major_pentatonic": (0, 2, 4, 7, 9),
    "minor_pentatonic": (0, 3, 5, 7, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "whole_tone": (0, 2, 4, 6, 8, 10),
    "chromatic": tuple(range(12)),
    # More modes and symmetric scales
    "major_blues": (0, 2, 3, 4, 7, 9),
    "mixolydian_b6": (0, 2, 4, 5, 7, 8, 10),
    "lydian_dominant": (0, 2, 4, 6, 7, 9, 10),
    "altered": (0, 1, 3, 4, 6, 8, 10),
    "double_harmonic": (0, 1, 4, 5, 7, 8, 11),
    "neapolitan_minor": (0, 1, 3, 5, 7, 8, 11),
    "neapolitan_major": (0, 1, 3, 5, 7, 9, 11),
    "ukrainian_dorian": (0, 2, 3, 6, 7, 9, 10),
    "persian": (0, 1, 4, 5, 6, 8, 11),
    "enigmatic": (0, 1, 4, 6, 8, 10, 11),
    "prometheus": (0, 2, 4, 6, 9, 10),
    "augmented": (0, 3, 4, 7, 8, 11),
    "diminished": (0, 2, 3, 5, 6, 8, 9, 11),
    "dominant_diminished": (0, 1, 3, 4, 6, 7, 9, 10),
    "bebop_dominant": (0, 2, 4, 5, 7, 9, 10, 11),
    "egyptian": (0, 2, 5, 7, 10),
    # Japanese pentatonics
    "hirajoshi": (0, 2, 3, 7, 8),
    "in": (0, 1, 5, 7, 8),
    "yo": (0, 2, 5, 7, 9),
    "iwato": (0, 1, 5, 6, 10),
    # Hindustani thaats (the seven-note parents of most ragas) and pentatonic ragas
    "kalyan": (0, 2, 4, 6, 7, 9, 11),
    "bilawal": (0, 2, 4, 5, 7, 9, 11),
    "khamaj": (0, 2, 4, 5, 7, 9, 10),
    "kafi": (0, 2, 3, 5, 7, 9, 10),
    "asavari": (0, 2, 3, 5, 7, 8, 10),
    "bhairavi": (0, 1, 3, 5, 7, 8, 10),
    "bhairav": (0, 1, 4, 5, 7, 8, 11),
    "marwa": (0, 1, 4, 6, 7, 9, 11),
    "purvi": (0, 1, 4, 6, 7, 8, 11),
    "todi": (0, 1, 3, 6, 7, 8, 11),
    "bhupali": (0, 2, 4, 7, 9),
    "durga": (0, 2, 5, 7, 9),
    "malkauns": (0, 3, 5, 8, 10),
    "hindol": (0, 4, 6, 9, 11),
    # Maqamat without quarter tones (rast, bayati and saba need them: not here)
    "hijaz": (0, 1, 4, 5, 7, 8, 10),
    "hijaz_kar": (0, 1, 4, 5, 7, 8, 11),
    "nahawand": (0, 2, 3, 5, 7, 8, 11),
    "kurd": (0, 1, 3, 5, 7, 8, 10),
    "nikriz": (0, 2, 3, 6, 7, 9, 10),
    "ajam": (0, 2, 4, 5, 7, 9, 11),
}
SCALE_ALIASES = {
    "ionian": "major", "aeolian": "minor", "natural_minor": "minor",
    "super_locrian": "altered", "byzantine": "double_harmonic", "octatonic": "diminished",
    "miyako_bushi": "in",
}

# MIDI 0-127 spans about 10.6 octaves; anything beyond this can't fit.
MAX_OCTAVES = 11

CHORD_QUALITIES: dict[str, tuple[int, ...]] = {
    "": (0, 4, 7),
    "maj": (0, 4, 7),
    "m": (0, 3, 7),
    "min": (0, 3, 7),
    "dim": (0, 3, 6),
    "aug": (0, 4, 8),
    "+": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "sus": (0, 5, 7),
    "5": (0, 7),
    "6": (0, 4, 7, 9),
    "m6": (0, 3, 7, 9),
    "7": (0, 4, 7, 10),
    "maj7": (0, 4, 7, 11),
    "M7": (0, 4, 7, 11),
    "m7": (0, 3, 7, 10),
    "mMaj7": (0, 3, 7, 11),
    "dim7": (0, 3, 6, 9),
    "m7b5": (0, 3, 6, 10),
    "7sus4": (0, 5, 7, 10),
    "add9": (0, 4, 7, 14),
    "madd9": (0, 3, 7, 14),
    "9": (0, 4, 7, 10, 14),
    "maj9": (0, 4, 7, 11, 14),
    "m9": (0, 3, 7, 10, 14),
}

# Keys conventionally spelled with flats.
FLAT_MAJOR_KEYS = {"F", "Bb", "Eb", "Ab", "Db", "Gb", "Cb"}
FLAT_MINOR_KEYS = {"D", "G", "C", "F", "Bb", "Eb", "Ab"}

_CHORD_SYMBOL = re.compile(r"^([A-G][#b]?)([^/]*)(?:/([A-G][#b]?))?$")
_ROMAN = re.compile(
    r"^(b|#)?(VII|VI|V|IV|III|II|I|vii|vi|v|iv|iii|ii|i)(°|o|dim|ø|\+|aug)?(maj7|7)?$"
)
_ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7}


@dataclass(frozen=True)
class Chord:
    symbol: str
    pitches: tuple[int, ...]


def scale_intervals(scale: str) -> tuple[int, ...]:
    key = scale.strip().lower().replace(" ", "_").replace("-", "_")
    key = SCALE_ALIASES.get(key, key)
    if key not in SCALES:
        raise ValueError(f"Unknown scale '{scale}'. Known scales: {', '.join(sorted(SCALES))}")
    return SCALES[key]


def _octave_root(root: str, octave: int) -> int:
    return 12 * (octave + 1) + pitch_class(root)


def _check_range(pitches: list[int]) -> list[int]:
    if pitches and (min(pitches) < MIDI_MIN or max(pitches) > MIDI_MAX):
        raise ValueError("Resulting pitches fall outside the MIDI range 0-127; use another octave")
    return pitches


def scale_pitches(root: str, scale: str = "major", octave: int = 4, octaves: int = 1) -> list[int]:
    """Ascending scale pitches over `octaves` octaves, ending on the top root."""
    if not 1 <= octaves <= MAX_OCTAVES:
        raise ValueError(f"octaves must be 1-{MAX_OCTAVES} (the MIDI range), got {octaves}")
    intervals = scale_intervals(scale)
    base = _octave_root(root, octave)
    pitches = [base + 12 * o + i for o in range(octaves) for i in intervals]
    return _check_range([*pitches, base + 12 * octaves])


def degree_pitch(root: str, scale: str, degree: int, octave: int = 4) -> int:
    """Pitch of a 1-based scale degree; 8 is the next octave's root, 0 the degree below 1."""
    intervals = scale_intervals(scale)
    octave_shift, index = divmod(degree - 1, len(intervals))
    return _check_range([_octave_root(root, octave) + 12 * octave_shift + intervals[index]])[0]


def chord(symbol: str, octave: int = 4) -> list[int]:
    """Pitches of a chord symbol ("Fm7", "C/E", "Bbmaj7") with the root in `octave`."""
    match = _CHORD_SYMBOL.match(symbol.strip())
    if not match:
        raise ValueError(f"Invalid chord symbol '{symbol}' (expected e.g. 'Am', 'F#m7', 'C/E')")
    root_name, quality, bass_name = match.groups()
    if quality not in CHORD_QUALITIES:
        known = ", ".join(repr(q) for q in CHORD_QUALITIES)
        raise ValueError(f"Unknown chord quality '{quality}' in '{symbol}'. Known: {known}")

    root = _octave_root(root_name, octave)
    pitches = [root + i for i in CHORD_QUALITIES[quality]]
    if bass_name:
        below_root = (pitch_class(root_name) - pitch_class(bass_name)) % 12 or 12
        pitches = [root - below_root, *pitches]
    return _check_range(pitches)


def invert(pitches: list[int], inversion: int) -> list[int]:
    """Move the lowest note up an octave per step (negative: highest note down)."""
    if not pitches:
        raise ValueError("Can't invert an empty chord")
    if abs(inversion) > MAX_OCTAVES * len(pitches):
        raise ValueError(f"Inversion {inversion} would move the chord outside the MIDI range")
    voiced = sorted(pitches)
    for _ in range(inversion):
        voiced = [*voiced[1:], voiced[0] + 12]
    for _ in range(-inversion):
        voiced = [voiced[-1] - 12, *voiced[:-1]]
    return _check_range(voiced)


def _uses_flats(key: str, intervals: tuple[int, ...]) -> bool:
    is_minor = len(intervals) > 2 and intervals[2] == 3
    return "b" in key or key in (FLAT_MINOR_KEYS if is_minor else FLAT_MAJOR_KEYS)


def _roman_quality(is_upper: bool, marker: str | None, seventh: str | None) -> str:
    if marker == "ø":
        return "m7b5"
    if marker in ("°", "o", "dim"):
        if seventh == "maj7":
            raise ValueError("A diminished chord can't take 'maj7'")
        return "dim7" if seventh else "dim"
    if marker in ("+", "aug"):
        if seventh:
            raise ValueError("Augmented sevenths aren't supported; use a chord symbol")
        return "aug"
    if seventh == "maj7":
        return "maj7" if is_upper else "mMaj7"
    if seventh == "7":
        return "7" if is_upper else "m7"
    return "" if is_upper else "m"


def roman_to_symbol(numeral: str, key: str, scale: str = "major") -> str:
    """Chord symbol for a roman numeral in a key: uppercase = major, lowercase = minor.

    Supports b/# prefixes, °/dim, ø, +/aug and 7/maj7 suffixes ("bVII", "vii°", "V7").
    """
    match = _ROMAN.match(numeral.strip())
    if not match:
        raise ValueError(
            f"Invalid roman numeral '{numeral}' (expected e.g. 'i', 'IV', 'V7', 'bVII', 'vii°')"
        )
    accidental, roman, marker, seventh = match.groups()
    intervals = scale_intervals(scale)
    if len(intervals) != 7:
        raise ValueError(f"Roman numerals need a 7-note scale; '{scale}' has {len(intervals)}")

    offset = {"b": -1, "#": 1}.get(accidental or "", 0)
    root_pc = (pitch_class(key) + intervals[_ROMAN_VALUES[roman.lower()] - 1] + offset) % 12
    flats = accidental == "b" or (accidental is None and _uses_flats(key, intervals))
    root_name = (FLAT_NAMES if flats else SHARP_NAMES)[root_pc]
    return root_name + _roman_quality(roman.isupper(), marker, seventh)


def progression(
    chords: str | list[str], key: str | None = None, scale: str = "major", octave: int = 4
) -> list[Chord]:
    """Resolve chord symbols and/or roman numerals ("i-VI-III-VII") to pitches."""
    tokens = re.split(r"[\s,\-]+", chords.strip()) if isinstance(chords, str) else chords
    resolved = []
    for token in (t for t in tokens if t):
        if _ROMAN.match(token):
            if key is None:
                raise ValueError(f"Roman numeral '{token}' needs a key")
            symbol = roman_to_symbol(token, key, scale)
        else:
            symbol = token
        resolved.append(Chord(symbol, tuple(chord(symbol, octave))))
    return resolved


# --- Voice leading and basslines ---------------------------------------------------------

BASS_MOVEMENTS = ("root", "root_fifth", "octave")
# How far (semitones) a led chord may drift from the first chord's register.
REGISTER_REACH = 12


def chord_root(symbol: str) -> int:
    """Pitch class of a chord symbol's root (a slash chord's bass is ignored)."""
    match = _CHORD_SYMBOL.match(symbol.strip())
    if not match:
        raise ValueError(f"Invalid chord symbol '{symbol}'")
    return pitch_class(match.group(1))


def _voicings(pitches: list[int], centre: float) -> list[list[int]]:
    """Every inversion of a chord, in every octave near `centre`."""
    found = []
    base = sorted(pitches)
    for inversion in range(len(base)):
        voiced = base[inversion:] + [p + 12 for p in base[:inversion]]
        for shift in range(-3, 4):
            moved = [p + 12 * shift for p in voiced]
            mean = sum(moved) / len(moved)
            if abs(mean - centre) <= REGISTER_REACH and MIDI_MIN <= moved[0] and (
                    moved[-1] <= MIDI_MAX):
                found.append(moved)
    return found


def _distance(previous: list[int], voicing: list[int]) -> int:
    """Total movement of the voices, each to the nearest voice of the next chord."""
    return sum(min(abs(p - q) for q in voicing) for p in previous) + sum(
        min(abs(q - p) for p in previous) for q in voicing)


def voice_lead(chords: list[list[int]]) -> list[list[int]]:
    """Voice each chord after the first with the least movement from the one before.

    Pitch classes are kept; the first chord stays as given and the others stay
    within an octave of its register.
    """
    if not chords:
        return []
    led = [sorted(chords[0])]
    centre = sum(led[0]) / len(led[0])
    for pitches in chords[1:]:
        options = _voicings(pitches, centre) or [sorted(pitches)]
        led.append(min(options, key=lambda v: (_distance(led[-1], v),
                                               abs(sum(v) / len(v) - centre))))
    return led


def bassline(chords: list[str], grid: str = "x...", step: float = 0.25,
             beats_per_chord: float = 4.0, octave: int = 2, movement: str = "root",
             start: float = 0.0, velocity: float = 0.8, key: str | None = None,
             scale: str = "major") -> list[Note]:
    """A bassline from chords: the step grid repeated over each chord, on its root.

    Args:
        grid: Step pattern (rhythm syntax: x hit, X accent, - tie, . rest).
        movement: root (every hit on the root), root_fifth (root, fifth, ...) or
            octave (root, the octave above, ...).
    """
    from fl_studio_mcp.music.rhythm import parse_grid

    if movement not in BASS_MOVEMENTS:
        raise ValueError(f"movement must be one of: {', '.join(BASS_MOVEMENTS)}")
    hits, steps = parse_grid(grid)
    if not hits:
        raise ValueError("The bass grid has no hits")
    span = steps * step
    repeats = max(1, round(beats_per_chord / span))
    offsets = {"root": (0, 0), "root_fifth": (0, 7), "octave": (0, 12)}[movement]
    notes = []
    for index, resolved in enumerate(progression(chords, key=key, scale=scale)):
        root = 12 * (octave + 1) + chord_root(resolved.symbol)
        chord_start = start + index * beats_per_chord
        number = 0
        for repeat in range(repeats):
            for hit in hits:
                time = chord_start + repeat * span + hit.step * step
                if time >= chord_start + beats_per_chord - 1e-9:
                    continue
                accent = min(1.0, velocity * 1.2) if hit.accent else velocity
                notes.append(Note(root + offsets[number % 2], time, hit.length * step, accent))
                number += 1
    return notes
