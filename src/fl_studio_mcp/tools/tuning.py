"""Finding out what a channel is tuned to, so parts can be made to agree.

A sample or preset needn't sound the note it is given: a kick sample has a
pitch of its own, a preset may be transposed. `fl_measure_pitch` plays one known
note on the channel alone, renders it and compares what was heard with what
was sent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.audio.pitch import semitones_to_pitch_class
from fl_studio_mcp.music.model import parse_pitch, pitch_class, pitch_name
from fl_studio_mcp.tools.audio import (
    DEFAULT_RENDER_TIMEOUT,
    DEFAULT_WINDOW_SECONDS,
    _fl,
    _put_back,
    analyze_file,
    render_stems,
)
from fl_studio_mcp.tools.piano_roll import write_notes
from fl_studio_mcp.utils.fl_render import RenderError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

# MIDI 60, which FL Studio calls C5: the note a sampler plays its sample unchanged at.
DEFAULT_TEST_NOTE = "C4"
DEFAULT_TEST_BEATS = 4.0
MAX_TEST_BEATS = 64.0
# A sound counts as in tune when it is no further than this from a note.
IN_TUNE_CENTS = 15
# Below this share of the pitched time on one note (in any octave), the sound
# has no steady pitch.
STEADY_SHARE = 0.6


def _test_render(channel: int, note: int, beats: float, timeout: float) -> tuple[dict, list[str]]:
    """Analysis of `note` played alone on `channel`, and what couldn't be put back after.

    The note goes into an unused pattern, which is rendered in pattern mode and
    emptied again. Raises RenderError.
    """
    mode = _fl("transport.getStatus").get("loop_mode")
    current = _fl("patterns.getAll").get("current")
    scratch = _fl("patterns.findEmpty")["index"]
    path: Path | None = None
    try:
        try:
            write_notes([{"midi": note, "time": 0, "duration": beats}], channel, scratch)
        except (ValueError, RuntimeError) as e:  # a bad note, or FL not answering
            raise RenderError(str(e)) from e
        if mode != "pattern":
            _fl("transport.setLoopMode", {"mode": "pattern"})
        rendered = render_stems([channel], timeout, keep_files=True)
        path = Path(rendered["stems"][0]["path"])
        analysis = analyze_file(path, DEFAULT_WINDOW_SECONDS)
        problems = [rendered["warning"]] if "warning" in rendered else []
    finally:
        left = []
        try:
            write_notes([], channel, scratch)
        except (ValueError, RuntimeError) as e:  # the other steps must still run
            left.append(f"the test note is still in pattern {scratch} ({e})")
        if current is not None and _put_back("patterns.select", {"index": current}):
            left.append(f"pattern {scratch} is still selected")
        if mode == "song" and _put_back("transport.setLoopMode", {"mode": "song"}):
            left.append("FL is still in pattern mode")
        if path is not None:
            path.unlink(missing_ok=True)
    if "error" in analysis:
        raise RenderError(analysis["error"])
    return analysis, problems + left


def _advice(sent: int, heard: dict, key_root: str | None) -> dict:
    """What the measurement means for the notes to write."""
    offset = heard["midi"] - sent
    # Octaves are a matter of taste (and of which overtone is loudest); what
    # makes parts clash is the pitch class.
    shift = semitones_to_pitch_class(heard["midi"], sent % 12)
    advice: dict = {"offset_semitones": offset, "transpose_to_correct": shift}
    if shift == 0 and abs(heard["cents"]) <= IN_TUNE_CENTS:
        octaves = "" if offset == 0 else (
            f", {abs(offset) // 12} octave(s) {'up' if offset > 0 else 'down'}"
        )
        advice["summary"] = f"In tune: the channel sounds the note it is given{octaves}."
    else:
        advice["summary"] = (
            f"The channel sounds {heard['note']} {heard['cents']:+d} cents when given "
            f"{pitch_name(sent)}. Write its notes {shift:+d} semitones from the ones you want "
            "to hear"
            + (f", and add fine_pitch {-heard['cents']:+d} to them."
               if abs(heard["cents"]) > IN_TUNE_CENTS else ".")
        )
    if key_root is not None:
        # The note to send so that the root is heard: nearest to the note tried.
        to_root = semitones_to_pitch_class(heard["midi"], pitch_class(key_root))
        advice["note_for_root"] = pitch_name(sent + to_root)
    return advice


def _unsteady(sent: int, heard: dict) -> str:
    """What to make of a sound that moves between notes while one note is held."""
    if parse_pitch(heard["ends_on"]) % 12 == sent % 12:
        return (
            f"The pitch glides: it only arrives on the note it is given ({heard['ends_on']}) "
            "late in the note, so short notes never reach their pitch and melodies sound "
            "out of key. The sound has a pitch envelope or portamento: find it with "
            "fl_get_plugin_params(search=\"pitch\"), shorten its time or lower its amount, "
            "and measure again."
        )
    return (
        "No steady pitch: the sound moves between notes while one note is held (a "
        "pitch sweep, a wide vibrato, or oscillators tuned apart), so melodies "
        "written for it won't come out as written. Look at the plugin's pitch "
        "parameters (fl_get_plugin_params(search=\"pitch\")), try a longer note, or use "
        "another sound for melodic parts."
    )


def register_tuning_tools(mcp: FastMCP) -> None:
    """Register the tuning tools with the MCP server."""

    @mcp.tool()
    def fl_measure_pitch(
        channel: int | str,
        note: int | str = DEFAULT_TEST_NOTE,
        beats: float = DEFAULT_TEST_BEATS,
        key_root: str | None = None,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Play one note on a channel alone and measure the pitch that comes out.

        Use it on every pitched sound you load, drums with a tone included (a
        kick has a note of its own), before writing its part: samples and
        presets often don't sound the note they are given, and parts tuned
        differently clash however right the written notes are. The note is
        put in an unused pattern, rendered like fl_render_stems and removed
        again; the piano roll is left showing this channel. Windows only.

        Args:
            channel: Channel index or role.
            note: The note to play (name with C4 = 60, or MIDI number). The
                default, C4, is the note a sampler plays its sample unchanged
                at (FL Studio shows it as C5), and what a step plays.
            beats: How long the note is held. Hold it as long as the notes
                you will write: a sound whose pitch drops as it rings (a kick)
                reads higher when it is cut short.
            key_root: The song's root ("F", "G#"): adds "note_for_root", the note
                to write on this channel so that the root is heard.
            timeout: Seconds to wait for the render.

        Returns {"sent", "heard": {"note", "midi", "hz", "cents", "share",
        "class_share", "ends_on", "voiced", "notes"} or null (no clear pitch: noise, or a very short
        sound), "summary", "loudness_lufs", "peak_dbfs"} or {"error": ...}.
        For a sound with a steady pitch also "offset_semitones" (heard minus
        sent), "transpose_to_correct" (semitones to add to the notes you write
        so the intended pitch class is heard) and "note_for_root".
        """
        try:
            index = resolve_channel(channel)
            sent = parse_pitch(note)
            if key_root is not None:
                pitch_class(key_root)
        except ValueError as e:
            return {"error": str(e)}
        if not 0 < beats <= MAX_TEST_BEATS:
            return {"error": f"beats must be more than 0 and at most {MAX_TEST_BEATS:g}"}
        try:
            with keyboard_lock:
                analysis, problems = _test_render(index, sent, beats, timeout)
        except RenderError as e:
            return {"error": str(e)}

        heard = analysis.get("pitch")
        result: dict = {
            "channel": index,
            "sent": pitch_name(sent),
            "heard": heard,
            "loudness_lufs": analysis["loudness_lufs"],
            "peak_dbfs": analysis["peak_dbfs"],
        }
        if heard is None:
            result["summary"] = (
                "No clear pitch: the channel was silent, or the sound is noise-like or "
                "too short to have one. It needs no tuning."
            )
        elif heard["class_share"] < STEADY_SHARE:
            result["summary"] = _unsteady(sent, heard)
        else:
            result.update(_advice(sent, heard, key_root))
        if problems:
            result["warning"] = "Not everything could be put back: " + "; ".join(problems)
        return result
