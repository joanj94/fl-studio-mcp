"""Notes, pitch names and musical time."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

MIDI_MIN = 0
MIDI_MAX = 127
DEFAULT_VELOCITY = 0.8
CENTER_PAN = 0.5

# Upper bound on notes one generator call may produce. Sizes come from AI-supplied
# arguments, so they're checked before anything is built.
MAX_NOTES = 10_000

SHARP_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
FLAT_NAMES = ("C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B")
LETTER_PITCH_CLASSES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_PITCH_NAME = re.compile(r"^([A-Ga-g])([#b]{0,2})(-?\d+)$")
_PITCH_CLASS_NAME = re.compile(r"^([A-Ga-g])([#b]{0,2})$")

Pitch = int | str


def _accidental_offset(accidentals: str) -> int:
    return accidentals.count("#") - accidentals.count("b")


def pitch_class(name: str) -> int:
    """Pitch class (0-11) of a note name without octave, e.g. "F#" -> 6."""
    match = _PITCH_CLASS_NAME.match(name.strip())
    if not match:
        raise ValueError(f"Invalid note name '{name}' (expected e.g. 'C', 'F#', 'Bb')")
    letter, accidentals = match.groups()
    return (LETTER_PITCH_CLASSES[letter.upper()] + _accidental_offset(accidentals)) % 12


def parse_pitch(value: Pitch) -> int:
    """MIDI number from an int or a scientific note name ("C4" = 60, "F#3", "Bb2")."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid pitch {value!r}")
    if isinstance(value, int):
        midi = value
    elif isinstance(value, str):
        match = _PITCH_NAME.match(value.strip())
        if not match:
            raise ValueError(f"Invalid pitch '{value}' (expected e.g. 'C4', 'F#3', 'Bb2' or 0-127)")
        letter, accidentals, octave = match.groups()
        midi = 12 * (int(octave) + 1) + LETTER_PITCH_CLASSES[letter.upper()]
        midi += _accidental_offset(accidentals)
    else:
        raise ValueError(f"Invalid pitch {value!r}")

    if not MIDI_MIN <= midi <= MIDI_MAX:
        raise ValueError(f"Pitch {value!r} is outside the MIDI range 0-127")
    return midi


def check_note_count(count: int) -> None:
    """Refuse to generate more than MAX_NOTES notes in one call."""
    if count > MAX_NOTES:
        raise ValueError(
            f"This would create {count} notes; the limit is {MAX_NOTES} per call. "
            "Generate the material in smaller pieces."
        )


def _number(data: dict[str, Any], keys: tuple[str, ...], default: float | None = None) -> float:
    """Read the first present key as a number; present-but-invalid values are errors."""
    for key in keys:
        if key in data:
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Note field '{key}' must be a number, got {value!r}")
            return float(value)
    if default is None:
        raise ValueError(f"Note {data!r} needs a value for {' or '.join(map(repr, keys))}")
    return default


def pitch_name(midi: int, prefer_flats: bool = False) -> str:
    """Scientific note name for a MIDI number (60 -> "C4")."""
    names = FLAT_NAMES if prefer_flats else SHARP_NAMES
    return f"{names[midi % 12]}{midi // 12 - 1}"


@dataclass(frozen=True)
class Note:
    """A note in beats (quarter notes). Immutable; transforms return new notes."""

    pitch: int
    start: float
    length: float
    velocity: float = DEFAULT_VELOCITY
    pan: float = CENTER_PAN
    slide: bool = False
    porta: bool = False

    def __post_init__(self) -> None:
        if not MIDI_MIN <= self.pitch <= MIDI_MAX:
            raise ValueError(f"Note pitch {self.pitch} is outside the MIDI range 0-127")
        if self.start < 0:
            raise ValueError(f"Note start {self.start} is before the pattern start (0)")
        if self.length <= 0:
            raise ValueError(f"Note length must be positive, got {self.length}")
        if not 0 <= self.velocity <= 1:
            raise ValueError(f"Note velocity must be 0-1, got {self.velocity}")
        if not 0 <= self.pan <= 1:
            raise ValueError(f"Note pan must be 0 (left) to 1 (right), got {self.pan}")

    @property
    def end(self) -> float:
        return self.start + self.length

    def to_dict(self) -> dict[str, Any]:
        """The note in fl_send_notes format; optional fields only when not default."""
        data: dict[str, Any] = {
            "midi": self.pitch,
            "time": self.start,
            "duration": self.length,
            "velocity": self.velocity,
        }
        if self.pan != CENTER_PAN:
            data["pan"] = self.pan
        if self.slide:
            data["slide"] = True
        if self.porta:
            data["porta"] = True
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        """Build a note from fl_send_notes format; "pitch"/"start"/"length" also accepted."""
        pitch = data.get("midi", data.get("pitch"))
        if pitch is None:
            raise ValueError(f"Note {data!r} needs a 'midi' (or 'pitch') value")
        return cls(
            pitch=parse_pitch(pitch),
            start=_number(data, ("time", "start"), 0.0),
            length=_number(data, ("duration", "length")),
            velocity=_number(data, ("velocity",), DEFAULT_VELOCITY),
            pan=_number(data, ("pan",), CENTER_PAN),
            slide=bool(data.get("slide", False)),
            porta=bool(data.get("porta", False)),
        )


@dataclass(frozen=True)
class TimeSignature:
    numerator: int = 4
    denominator: int = 4

    def __post_init__(self) -> None:
        if self.numerator < 1:
            raise ValueError(f"Time signature numerator must be >= 1, got {self.numerator}")
        if self.denominator not in (1, 2, 4, 8, 16, 32):
            raise ValueError(
                f"Time signature denominator must be a power of 2, got {self.denominator}"
            )

    @property
    def beats_per_bar(self) -> float:
        """Bar length in quarter notes (6/8 -> 3.0)."""
        return self.numerator * 4 / self.denominator


FOUR_FOUR = TimeSignature()


def bars_to_beats(bars: float, time_signature: TimeSignature = FOUR_FOUR) -> float:
    return bars * time_signature.beats_per_bar


def position(bar: int, beat: float = 1, time_signature: TimeSignature = FOUR_FOUR) -> float:
    """Beats from the start for a 1-based bar and beat (bar 2, beat 3 in 4/4 -> 6.0)."""
    if bar < 1 or beat < 1:
        raise ValueError(f"Bars and beats are 1-based, got bar {bar}, beat {beat}")
    return (bar - 1) * time_signature.beats_per_bar + (beat - 1)
