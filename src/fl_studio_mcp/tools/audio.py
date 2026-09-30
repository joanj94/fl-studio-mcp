"""Rendering the project to audio and measuring the result.

`fl_render` drives FL Studio's export dialog (`utils/fl_render.py`);
`audio_analyze` measures any WAV file and doesn't touch FL.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_render import RenderError, render_wav

if TYPE_CHECKING:
    from fastmcp import FastMCP

RENDER_DIR_ENV = "FL_MCP_RENDER_DIR"
DEFAULT_RENDER_TIMEOUT = 300.0
DEFAULT_WINDOW_SECONDS = 4.0
MISSING_NUMPY = (
    "Audio analysis needs numpy, which is an optional dependency: "
    "install it with `uv sync --extra analysis`."
)


def get_render_dir() -> Path:
    """Folder renders go to by default; FL_MCP_RENDER_DIR overrides the temp folder."""
    override = os.environ.get(RENDER_DIR_ENV)
    folder = Path(override) if override else Path(tempfile.gettempdir()) / "fl-studio-mcp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _new_render_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = get_render_dir() / f"render-{stamp}.wav"
    number = 2
    while path.exists():
        path = get_render_dir() / f"render-{stamp}-{number}.wav"
        number += 1
    return path


def analyze_file(path: Path, window_seconds: float) -> dict:
    """Measurements of a WAV file, or {"error": ...}."""
    try:
        from fl_studio_mcp.audio.analysis import analyze
        from fl_studio_mcp.audio.wav import read_wav
    except ImportError:
        return {"error": MISSING_NUMPY}
    if not window_seconds > 0:
        return {"error": f"window_seconds must be more than 0, got {window_seconds}"}
    try:
        samples, rate = read_wav(path)
        return analyze(samples, rate, window_seconds)
    except (OSError, ValueError) as e:
        return {"error": f"Could not analyse {path}: {e}"}
    except MemoryError:
        return {"error": f"Could not analyse {path}: not enough memory for a file this long"}


def register_audio_tools(mcp: FastMCP) -> None:
    """Register render and analysis tools with the MCP server."""

    @mcp.tool()
    def fl_render(
        path: str | None = None,
        analyze: bool = True,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Render the project to a WAV file and (by default) measure it.

        Renders what FL Studio would play: the active pattern in pattern mode,
        the playlist in song mode (see fl_set_loop_mode). It works through FL's
        export dialog by keystrokes, so FL comes to the front and must not be
        showing a dialog; the dialog's last-used quality settings apply.
        Windows only.

        Args:
            path: Where to write the .wav; it must not exist yet. Default: a new
                file in the render folder.
            analyze: Also return the measurements audio_analyze gives.
            timeout: Seconds to wait for the render to finish.

        Returns {"path", "mode", "render_seconds", "analysis"?} or {"error": ...}.
        """
        target = Path(path) if path else _new_render_path()
        if target.suffix.lower() != ".wav":
            return {"error": f"The render path must end in .wav, got {target.name!r}"}
        mode = call("transport.getStatus").get("loop_mode")
        try:
            seconds = render_wav(target.resolve(), timeout)
        except RenderError as e:
            return {"error": str(e)}
        result = {"path": str(target.resolve()), "mode": mode, "render_seconds": round(seconds, 1)}
        if analyze:
            result["analysis"] = analyze_file(target, DEFAULT_WINDOW_SECONDS)
        return result

    @mcp.tool()
    def audio_analyze(path: str, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> dict:
        """Measure a WAV file: loudness, peaks, frequency balance, stereo image and key.

        Doesn't touch FL Studio. Returns:
            duration_seconds, sample_rate, channels
            loudness_lufs: integrated programme loudness (ITU-R BS.1770)
            peak_dbfs, rms_dbfs, crest_factor_db (peak minus RMS: low = heavily compressed)
            clipped_samples: samples at full scale
            silence_seconds: silence at the start and end
            frequency_balance: share of energy per band (sub, bass, low_mid, mid,
                high_mid, high) in percent and dB
            stereo: left/right correlation (1 mono, 0 wide, below 0 phase problems)
                and side_to_mid_db (higher = wider)
            key: estimated key with a confidence and the runner-up
            loudness_over_time: loudness of consecutive windows, to see sections

        Args:
            path: The .wav file (16/24/32-bit PCM or float).
            window_seconds: Window length for loudness_over_time.
        """
        return analyze_file(Path(path), window_seconds)
