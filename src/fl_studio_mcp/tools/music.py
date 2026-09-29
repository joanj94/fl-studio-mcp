"""Music toolkit tools: build and transform notes without touching FL Studio.

Every tool returns notes in the same format fl_send_notes accepts, so an AI can
generate, combine and preview material before writing it to the piano roll.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

from fl_studio_mcp.music.model import Note, pitch_name
from fl_studio_mcp.music.rhythm import rhythm_notes, roll
from fl_studio_mcp.music.theory import (
    CHORD_QUALITIES,
    SCALES,
    degree_pitch,
    invert,
    progression,
    scale_pitches,
)
from fl_studio_mcp.music.transform import (
    ARPEGGIO_MODES,
    apply_operations,
    describe_operations,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

PITCH_CONVENTION = (
    "Scientific pitch names: C4 = MIDI 60 (FL Studio's piano roll labels it C5). "
    "Sharps (#) and flats (b) are accepted, e.g. 'F#3', 'Bb2'."
)
GRID_SYNTAX = (
    "x = hit, X = accented hit, _ = tie (extend previous hit), . or - = rest; "
    "spaces and | are ignored; E(pulses,steps[,rotation]) inserts a Euclidean rhythm."
)


def _notes_result(notes: list[Note], **extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "notes": [n.to_dict() for n in notes],
        "count": len(notes),
        "start": min((n.start for n in notes), default=0),
        "end": max((n.end for n in notes), default=0),
    }


def _safe(build: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a tool body, turning invalid musical input into an error the AI can read.

    TypeError is included as a backstop for malformed values inside untyped
    arguments (e.g. operation parameters of the wrong type).
    """
    try:
        return build()
    except (ValueError, TypeError) as e:
        return {"error": str(e)}


def register_music_tools(mcp: FastMCP) -> None:
    """Register the note-building tools with the MCP server."""

    @mcp.tool()
    def music_reference() -> dict:
        """List the vocabulary the music tools understand.

        Returns scales, chord qualities, transform operations with their
        parameters, arpeggio modes, rhythm grid syntax and the pitch naming convention.
        """
        return {
            "scales": sorted(SCALES),
            "chord_qualities": [q or "(major triad)" for q in CHORD_QUALITIES],
            "transform_operations": describe_operations(),
            "arpeggio_modes": list(ARPEGGIO_MODES),
            "grid_syntax": GRID_SYNTAX,
            "pitch_names": PITCH_CONVENTION,
            "time": "All times and lengths are in beats (quarter notes) from the pattern start.",
        }

    @mcp.tool()
    def music_scale(root: str, scale: str = "major", octave: int = 4, octaves: int = 1) -> dict:
        """Get the pitches of a scale, e.g. root="F", scale="minor", octave=3.

        Args:
            root: Root note name without octave ("C", "F#", "Bb").
            scale: Scale name; see music_reference for the full list.
            octave: Octave of the root (C4 = MIDI 60).
            octaves: How many octaves to span.
        """
        def build() -> dict[str, Any]:
            pitches = scale_pitches(root, scale, octave, octaves)
            return {"pitches": pitches, "names": [pitch_name(p) for p in pitches]}

        return _safe(build)

    @mcp.tool()
    def music_chords(
        chords: list[str],
        key: str | None = None,
        scale: str = "major",
        octave: int = 4,
        beats_per_chord: float = 4.0,
        start: float = 0.0,
        velocity: float = 0.8,
        inversion: int = 0,
    ) -> dict:
        """Build a chord progression as notes, one chord after another.

        Args:
            chords: Chord symbols ("Fm7", "C/E") and/or roman numerals ("i", "VI", "V7",
                "bVII"). Roman numerals need `key`; uppercase = major, lowercase = minor.
            key: Key root for roman numerals ("F", "C#").
            scale: Scale the numerals refer to ("minor", "harmonic_minor", ...).
            octave: Octave of each chord's root.
            beats_per_chord: Length of each chord in beats.
            start: Beat where the first chord starts.
            inversion: Inversion applied to every chord (1 = first, -1 = drop the top note).
        """
        def build() -> dict[str, Any]:
            resolved = progression(chords, key=key, scale=scale, octave=octave)
            laid_out = []
            notes = []
            for i, c in enumerate(resolved):
                chord_start = start + i * beats_per_chord
                pitches = invert(list(c.pitches), inversion)
                laid_out.append({"symbol": c.symbol, "pitches": pitches,
                                 "names": [pitch_name(p) for p in pitches],
                                 "start": chord_start})
                notes += [Note(p, chord_start, beats_per_chord, velocity) for p in pitches]
            return _notes_result(notes, chords=laid_out)

        return _safe(build)

    @mcp.tool()
    def music_degrees(
        degrees: list[int | None],
        key: str,
        scale: str = "major",
        octave: int = 4,
        step: float = 0.5,
        start: float = 0.0,
        lengths: list[float] | None = None,
        velocity: float = 0.8,
    ) -> dict:
        """Write a melody as scale degrees: 1 = root, 8 = root an octave up, 0 = below root.

        Each degree takes `step` beats; None is a rest.

        Args:
            degrees: Scale degrees in order, e.g. [1, 3, 5, None, 8].
            key: Root note name ("A", "F#").
            scale: Scale name; see music_reference.
            octave: Octave of degree 1.
            step: Beats between consecutive degrees.
            start: Beat of the first degree.
            lengths: Optional note length per degree (default: `step`).
        """
        def build() -> dict[str, Any]:
            if lengths is not None and len(lengths) != len(degrees):
                raise ValueError(
                    f"lengths has {len(lengths)} values but there are {len(degrees)} degrees"
                )
            notes = [
                Note(degree_pitch(key, scale, d, octave), start + i * step,
                     lengths[i] if lengths else step, velocity)
                for i, d in enumerate(degrees)
                if d is not None
            ]
            return _notes_result(notes)

        return _safe(build)

    @mcp.tool()
    def music_rhythm(
        pattern: str,
        pitches: list[int | str],
        step: float = 0.25,
        start: float = 0.0,
        velocity: float = 0.8,
        accent_velocity: float = 1.0,
        gate: float = 1.0,
        repeat: int = 1,
    ) -> dict:
        """Turn a step-grid pattern into notes; every hit plays all `pitches`.

        Grid: x = hit, X = accent, _ = tie, . or - = rest, | and spaces ignored,
        E(3,8) = Euclidean rhythm. Example: "X...x...|X...x.x." with step 0.25.

        Args:
            pattern: The grid.
            pitches: One pitch, or several for a chord ("C2", 36, ...).
            step: Beats per grid step (0.25 = 16ths, 0.5 = 8ths).
            start: Beat where the pattern starts.
            gate: Fraction of each hit that sounds (0-1], for shorter notes.
            repeat: How many times to play the pattern back to back.
        """
        return _safe(lambda: _notes_result(rhythm_notes(
            pattern, pitches, step, start, velocity, accent_velocity, gate, repeat
        )))

    @mcp.tool()
    def music_roll(
        start: float,
        length: float,
        rates: list[int],
        pitch: int | str,
        velocity_start: float = 0.6,
        velocity_end: float = 1.0,
    ) -> dict:
        """A roll that speeds up, e.g. for fills and build-ups.

        The span is split evenly between `rates` (hits per beat): rates [1, 2, 4, 8]
        over 8 beats gives 2 beats each of quarters, eighths, sixteenths and 32nds.
        Velocity ramps from velocity_start to velocity_end.
        """
        return _safe(lambda: _notes_result(
            roll(start, length, rates, pitch, velocity_start, velocity_end)
        ))

    @mcp.tool()
    def music_transform(notes: list[dict], operations: list[dict]) -> dict:
        """Apply transformations to notes, in order.

        Args:
            notes: Notes in fl_send_notes format (as returned by the other music tools).
            operations: e.g. [{"op": "arpeggiate", "step": 0.25, "mode": "updown"},
                {"op": "transpose", "semitones": 12}, {"op": "humanize", "seed": 1}].
                See music_reference for every operation and its parameters.
        """
        def build() -> dict[str, Any]:
            parsed = [Note.from_dict(n) for n in notes]
            return _notes_result(apply_operations(parsed, operations))

        return _safe(build)
