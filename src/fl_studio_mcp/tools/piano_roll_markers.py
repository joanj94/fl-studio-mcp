"""Piano roll markers: key/scale, time signature, pattern length and plain markers.

They live in a pattern's piano roll and only `flpianoroll` can reach them, so they go
through FL's own script runner (utils/gopher.py, FL started with fl_launch). A key
marker makes FL highlight the scale's rows; a pattern length marker ends the pattern
there whatever its notes.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from fl_studio_mcp.music.model import pitch_class
from fl_studio_mcp.music.theory import scale_intervals
from fl_studio_mcp.tools.assistant import HINT
from fl_studio_mcp.tools.piano_roll import _select_target
from fl_studio_mcp.utils import gopher

if TYPE_CHECKING:
    from fastmcp import FastMCP

# flpianoroll.Marker.mode values.
MODES = {"plain": 0, "pattern_length": 6, "time_signature": 8, "key": 12}
KINDS = {mode: kind for kind, mode in MODES.items()}
DENOMINATORS = (1, 2, 4, 8, 16, 32)

READ_MARKERS = (
    "[{'time': flp.score.getMarker(i).time / flp.score.PPQ, 'mode': flp.score.getMarker(i).mode,"
    " 'name': flp.score.getMarker(i).name, 'tsnum': flp.score.getMarker(i).tsnum,"
    " 'tsden': flp.score.getMarker(i).tsden, 'root': flp.score.getMarker(i).scale_root,"
    " 'helper': flp.score.getMarker(i).scale_helper} for i in range(flp.score.markerCount)]"
)
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def scale_helper(root: str, scale: str) -> str:
    """FL's scale_helper: twelve 1/0 flags from C, 1 for the scale's pitch classes."""
    root_class = pitch_class(root)
    members = {(root_class + interval) % 12 for interval in scale_intervals(scale)}
    return ",".join("1" if pc in members else "0" for pc in range(12))


def _describe(marker: dict) -> dict:
    kind = KINDS.get(marker["mode"], f"mode {marker['mode']}")
    described = {"time": round(marker["time"], 6), "kind": kind}
    if kind == "key":
        described.update(name=marker["name"], root=NOTE_NAMES[marker["root"] % 12],
                         scale_helper=marker["helper"])
    elif kind == "time_signature":
        described["signature"] = f"{marker['tsnum']}/{marker['tsden']}"
    elif kind == "plain":
        described["name"] = marker["name"]
    return described


def _marker_code(kind: str, time: float, name: str | None, root: str | None,
                 scale: str | None, numerator: int | None, denominator: int | None) -> str:
    """The flpianoroll code that adds one marker; raises ValueError for a bad one."""
    if kind not in MODES:
        raise ValueError(f"kind must be one of: {', '.join(MODES)}")
    if not math.isfinite(time) or time < 0:
        raise ValueError("time must be 0 or more (beats)")
    fields: dict[str, object] = {"mode": MODES[kind]}
    if kind == "key":
        if root is None or scale is None:
            raise ValueError("A key marker needs root and scale, e.g. root='F', scale='minor'")
        fields.update(scale_root=pitch_class(root), scale_helper=scale_helper(root, scale),
                      name=name or f"{root} {scale}")
    elif kind == "time_signature":
        if numerator is None or denominator is None:
            raise ValueError("A time signature marker needs numerator and denominator")
        if not 1 <= numerator <= 32 or denominator not in DENOMINATORS:
            raise ValueError(f"numerator must be 1-32 and denominator one of {DENOMINATORS}")
        fields.update(tsnum=numerator, tsden=denominator)
    elif kind == "plain":
        if not name:
            raise ValueError("A plain marker needs a name")
        fields["name"] = name
    lines = ["import flpianoroll as flp", "_m = flp.Marker()",
             f"_m.time = round({float(time)!r} * flp.score.PPQ)"]
    lines += [f"_m.{key} = {value!r}" for key, value in fields.items()]
    lines.append("flp.score.addMarker(_m)")
    return "\n".join(lines) + "\n"


def _read() -> list[dict]:
    return [_describe(m) for m in gopher.run_piano_roll_python(
        "import flpianoroll as flp", returns=READ_MARKERS)]


def _prepare(channel: int | str | None, pattern: int | None) -> str | None:
    if gopher.page_url() is None:
        return f"Piano roll markers need FL's own script runner. {HINT}"
    return _select_target(channel, pattern)


def register_piano_roll_marker_tools(mcp: FastMCP) -> None:
    """Register piano roll marker tools with the MCP server."""

    @mcp.tool()
    def fl_add_piano_roll_marker(kind: str, time: float = 0, name: str | None = None,
                                 root: str | None = None, scale: str | None = None,
                                 numerator: int | None = None, denominator: int | None = None,
                                 channel: int | str | None = None,
                                 pattern: int | None = None) -> dict:
        """Add a marker to a pattern's piano roll (needs fl_launch).

        Kinds: "key" (FL highlights the scale's rows: root + scale, any scale
        music_scale knows), "time_signature" (numerator/denominator from there on),
        "pattern_length" (the pattern ends at `time`), "plain" (a named marker).

        Args:
            kind: key, time_signature, pattern_length or plain.
            time: Where, in beats from the pattern's start.
            name: The marker's label (plain markers need one; key markers default
                to "<root> <scale>").
            root, scale: For key markers, e.g. "F", "minor".
            numerator, denominator: For time signature markers, e.g. 7, 8.
            channel, pattern: Which piano roll (default: the one showing).

        Returns {"markers": the pattern's markers after the change} or {"error"}.
        """
        try:
            code = _marker_code(kind, time, name, root, scale, numerator, denominator)
        except ValueError as e:
            return {"error": str(e)}
        error = _prepare(channel, pattern)
        if error:
            return {"error": error}
        try:
            gopher.run_piano_roll_python(code)
            return {"markers": _read()}
        except gopher.GopherError as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_get_piano_roll_markers(channel: int | str | None = None,
                                  pattern: int | None = None) -> dict:
        """List the markers in a pattern's piano roll (needs fl_launch).

        Returns {"markers": [{"time" (beats), "kind", ...}]} or {"error"}.
        """
        error = _prepare(channel, pattern)
        if error:
            return {"error": error}
        try:
            return {"markers": _read()}
        except gopher.GopherError as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_clear_piano_roll_markers(channel: int | str | None = None,
                                    pattern: int | None = None) -> dict:
        """Delete every marker in a pattern's piano roll (needs fl_launch).

        Returns {"cleared": how many} or {"error"}.
        """
        error = _prepare(channel, pattern)
        if error:
            return {"error": error}
        try:
            count = len(_read())
            gopher.run_piano_roll_python("import flpianoroll as flp\n"
                                         "flp.score.clearMarkers(True)\n")
            return {"cleared": count - len(_read())}
        except gopher.GopherError as e:
            return {"error": str(e)}
