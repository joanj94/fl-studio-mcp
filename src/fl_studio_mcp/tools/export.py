"""Exporting a finished song: measured from a WAV, kept as an MP3.

Every song made so far ended the same way: render a WAV, measure it (loudness,
peaks, the technical checks of audio_qc), render the MP3 that is kept, delete the
WAV. An MP3 can't be measured here and MP3 encoding moves the peaks (seen on the
user's exports: 0.7-1.2 dBTP from a -0.1 dB ceiling), so the measuring always goes
through a WAV of the same render.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.audio import (
    DEFAULT_RENDER_TIMEOUT,
    DEFAULT_WINDOW_SECONDS,
    MISSING_NUMPY,
    _new_render_path,
    analyze_file,
)
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_render import RenderError, render_audio

if TYPE_CHECKING:
    from fastmcp import FastMCP


def song_path(folder: Path, name: str, when: datetime) -> Path:
    """"<name as a slug>-<YYYYMMDD-HHMM>.mp3" in `folder`; raises ValueError."""
    slug = re.sub(r"[^a-z0-9#]+", "-", name.lower()).strip("-")
    if not slug:
        raise ValueError(f"The song needs a name with letters or digits, got {name!r}")
    return folder / f"{slug}-{when.strftime('%Y%m%d-%H%M')}.mp3"


def measure(wav: Path) -> dict:
    """Loudness, peaks and audio_qc's problems of a render; raises ValueError."""
    from fl_studio_mcp.audio.decode import load_audio
    from fl_studio_mcp.audio.qc import check

    analysis = analyze_file(wav, DEFAULT_WINDOW_SECONDS, tempo=False)
    if "error" in analysis:
        raise ValueError(analysis["error"])
    samples, rate = load_audio(wav)
    qc = check(samples, rate)
    return {"seconds": qc["duration_seconds"], "loudness_lufs": analysis["loudness_lufs"],
            "peak_dbfs": analysis["peak_dbfs"], "true_peak_dbtp": qc["true_peak_dbtp"],
            "clipped_samples": analysis["clipped_samples"], "problems": qc["problems"]}


def export_song(name: str, folder: Path, timeout: float) -> dict:
    """fl_export_song's work; raises RenderError or ValueError."""
    target = song_path(folder, name, datetime.now())
    if target.exists():
        raise ValueError(f"{target} already exists; wait a minute or change the name")
    if not folder.is_dir():
        raise ValueError(f"The folder {folder} does not exist")
    status = call("transport.getStatus")
    if "error" in status:
        raise RenderError(status["error"])
    mode = status.get("loop_mode")
    if mode != "song":
        switched = call("transport.setLoopMode", {"mode": "song"})
        if "error" in switched:
            raise RenderError(switched["error"])
    wav = _new_render_path("export")
    try:
        render_audio(wav, timeout)
        measured = measure(wav)
        seconds = render_audio(target, timeout)
    finally:
        wav.unlink(missing_ok=True)
        if mode == "pattern":
            call("transport.setLoopMode", {"mode": "pattern"})
    return {"mp3": str(target), "render_seconds": round(seconds, 1), **measured,
            "note": "Measured on a WAV of the same render (deleted); MP3 encoding "
                    "can raise the true peak by about a dB."}


def register_export_tools(mcp: FastMCP) -> None:
    """Register the song export tool with the MCP server."""

    @mcp.tool()
    def fl_export_song(name: str, folder: str | None = None,
                       timeout: float = DEFAULT_RENDER_TIMEOUT) -> dict:
        """Export the finished song as an MP3 and say how it measures.

        Renders the playlist (song mode, switched on for the export and back) to a
        WAV, measures it (loudness, peak, true peak, clipping and audio_qc's checks:
        DC offset, clicks, dropouts, phase), renders the MP3, and deletes the WAV.
        The file is "<name>-<date>-<time>.mp3". Windows only.

        Args:
            name: The song's name, e.g. "night drive 150bpm Am".
            folder: Where the MP3 goes (default: the user's Music folder).
            timeout: Seconds to wait for each render.

        Returns {"mp3", "seconds", "loudness_lufs", "peak_dbfs", "true_peak_dbtp",
        "clipped_samples", "problems"} or {"error"}.
        """
        where = Path(folder) if folder else Path.home() / "Music"
        try:
            return export_song(name, where, timeout)
        except ImportError:
            return {"error": MISSING_NUMPY}
        except (RenderError, ValueError, OSError) as e:
            return {"error": str(e)}
