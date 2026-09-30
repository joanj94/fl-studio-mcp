"""Rendering the project to audio and measuring the result.

`fl_render` drives FL Studio's export dialog (`utils/fl_render.py`);
`fl_render_stems` does it once per channel, soloed, to show what each part
contributes; `audio_analyze` measures any WAV file and doesn't touch FL.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_render import EXPORT_ENTRIES, RenderError, render_audio
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.roles import resolve_channel

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


def _new_render_path(prefix: str = "render") -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = get_render_dir() / f"{prefix}-{stamp}.wav"
    number = 2
    while path.exists():
        path = get_render_dir() / f"{prefix}-{stamp}-{number}.wav"
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


# Below this peak a stem counts as silent: the channel plays nothing in what was rendered.
SILENT_PEAK_DBFS = -90.0


def _stem_summary(analysis: dict) -> dict:
    """The few numbers that tell what one channel adds to the mix."""
    if "error" in analysis:
        return {"error": analysis["error"]}
    if analysis["peak_dbfs"] <= SILENT_PEAK_DBFS:
        return {"silent": True}
    return {
        "silent": False,
        "loudness_lufs": analysis["loudness_lufs"],
        "peak_dbfs": analysis["peak_dbfs"],
        "clipped_samples": analysis["clipped_samples"],
        "frequency_percent": {
            band: values["percent"] for band, values in analysis["frequency_balance"].items()
        },
        "stereo_correlation": (analysis.get("stereo") or {}).get("correlation"),  # None: mono
        "key": analysis["key"],
        "pitch": analysis["pitch"],
    }


def _fl(action: str, params: dict | None = None) -> dict:
    """A controller command; raises RenderError if FL can't run it."""
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise RenderError(str(e)) from e
    if "error" in result:
        raise RenderError(result["error"])
    return result


MASTER_TRACK = 0


def render_stems(
    indexes: list[int] | None, timeout: float, keep_files: bool, master_effects: bool = True
) -> dict:
    """Render each channel soloed and summarise it; raises RenderError.

    With `master_effects` off, the master track's effects are switched off for
    the renders and on again afterwards: a limiter there would bring every
    stem up to the same ceiling and hide how loud each part really is.

    Soloing a channel and taking the solo off again leaves every channel
    unmuted, so the channels that were muted are muted again afterwards. A solo
    that was on comes back that way too: seen live, FL reports a solo as every
    other channel muted, and muting every other channel reads as a solo.
    """
    rack = _fl("channels.getAll").get("channels", [])
    missing = [i for i in indexes or [] if i >= len(rack)]
    if missing:
        raise RenderError(
            f"There is no channel {missing[0]}: the rack has channels 0-{len(rack) - 1}"
        )
    wanted = [c for c in rack if not c.get("is_muted")] if indexes is None else [
        rack[i] for i in indexes
    ]
    muted = [c["index"] for c in rack if c.get("is_muted")]
    bypassed: list[int] = []
    if not master_effects:
        bypassed = [
            effect["slot"]
            for effect in _fl("mixer.getTrackEffects", {"track": MASTER_TRACK}).get("effects", [])
            if effect.get("enabled")
        ]
    stems: list[dict] = []
    soloed: int | None = None
    try:
        for slot in bypassed:
            _fl("mixer.setEffect", {"track": MASTER_TRACK, "slot": slot, "enabled": False})
        for channel in wanted:
            soloed = channel["index"]
            _fl("channels.solo", {"index": soloed, "solo": True})
            path = _new_render_path(f"stem-{soloed}")
            render_audio(path, timeout)
            summary = _stem_summary(analyze_file(path, DEFAULT_WINDOW_SECONDS))
            if keep_files:
                summary["path"] = str(path)
            else:
                path.unlink(missing_ok=True)
            stems.append({"index": soloed, "name": channel.get("name"), **summary})
            _fl("channels.solo", {"index": soloed, "solo": False})
            soloed = None
    finally:
        problems = []
        if soloed is not None:
            problems.append(_put_back("channels.solo", {"index": soloed, "solo": False}))
        problems.extend(
            _put_back("channels.mute", {"index": index, "muted": True}) for index in muted
        )
        problems.extend(
            _put_back("mixer.setEffect", {"track": MASTER_TRACK, "slot": slot, "enabled": True})
            for slot in bypassed
        )
    result: dict = {"stems": stems}
    if any(problems):
        result["warning"] = (
            "Mute and solo states or the master's effects could not all be put back: "
            + "; ".join(problem for problem in problems if problem)
        )
    return result


def _put_back(action: str, params: dict) -> str | None:
    """Restore a changed state; returns what went wrong instead of raising."""
    try:
        return call(action, params).get("error")
    except RuntimeError as e:
        return str(e)


def register_audio_tools(mcp: FastMCP) -> None:
    """Register render and analysis tools with the MCP server."""

    @mcp.tool()
    def fl_render(
        path: str | None = None,
        analyze: bool = True,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Render the project to a WAV or MP3 file and (a WAV, by default) measure it.

        Renders what FL Studio would play: the active pattern in pattern mode,
        the playlist in song mode (see fl_set_loop_mode). It works through FL's
        own export dialog, which flashes up while FL stays in the background; FL
        must not be showing a dialog. The dialog's last-used quality settings
        apply. Windows only.

        Render a .wav to measure the mix, and an .mp3 for the song to keep or
        share (about a tenth of the size): an MP3 can't be measured here.

        Args:
            path: Where to write the .wav or .mp3; it must not exist yet.
                Default: a new .wav in the render folder.
            analyze: Also return the measurements audio_analyze gives (WAV only).
            timeout: Seconds to wait for the render to finish.

        Returns {"path", "mode", "render_seconds", "analysis"?} or {"error": ...}.
        """
        target = Path(path) if path else _new_render_path()
        suffix = target.suffix.lower()
        if suffix not in EXPORT_ENTRIES:
            return {"error": f"The render path must end in .wav or .mp3, got {target.name!r}"}
        mode = call("transport.getStatus").get("loop_mode")
        try:
            seconds = render_audio(target.resolve(), timeout)
        except RenderError as e:
            return {"error": str(e)}
        result = {"path": str(target.resolve()), "mode": mode, "render_seconds": round(seconds, 1)}
        if analyze and suffix == ".wav":
            result["analysis"] = analyze_file(target, DEFAULT_WINDOW_SECONDS)
        return result

    @mcp.tool()
    def fl_render_stems(
        channels: list[int | str] | None = None,
        keep_files: bool = False,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
        master_effects: bool = True,
    ) -> dict:
        """Render each channel on its own and measure it, to see what every part adds to the mix.

        Each channel is soloed and rendered like fl_render (pattern or song, per
        FL's mode), so it is heard through its mixer track and the master. Use
        it to balance levels: compare the stems' loudness and where their
        energy sits, change channel or mixer volumes, and render again. Takes
        about as long as one fl_render per channel. Windows only.

        Args:
            channels: Channel indexes or roles to render. Default: every channel
                that isn't muted.
            keep_files: Keep the stem WAVs and return their paths.
            master_effects: False switches the master track's effects off for
                these renders (and on again after). Do that to balance levels
                when the master has a limiter or maximizer: through it every
                loud stem peaks at the same ceiling and the numbers tell
                nothing. A stem peaking above 0 dBFS then is too hot by that
                much.
            timeout: Seconds to wait for each render.

        Returns {"mode", "stems": [{"index", "name", "silent", "loudness_lufs",
        "peak_dbfs", "clipped_samples", "frequency_percent": {band: percent},
        "stereo_correlation", "key", "pitch"}]} or {"error": ...}. A silent stem
        (the channel plays nothing in what was rendered) has only "silent": true.
        "pitch" is the note the stem plays most ({"note", "cents", "share",
        "notes"}, null for unpitched sounds). A part whose key or notes differ
        from what you wrote is tuned away from the notes it is given:
        fl_measure_pitch tells by how much.
        """
        try:
            indexes = None if channels is None else [resolve_channel(c) for c in channels]
        except ValueError as e:
            return {"error": str(e)}
        try:
            with keyboard_lock:
                mode = _fl("transport.getStatus").get("loop_mode")
                result = render_stems(indexes, timeout, keep_files, master_effects)
        except RenderError as e:
            return {"error": str(e)}
        return {"mode": mode, **result}

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
            pitch: the note played most of the time ({"note", "midi", "hz", "cents"
                off that note, "share" of the pitched time, "voiced" share of the
                sound that has a pitch, "notes"}); null for unpitched sounds.
                Meant for single sounds and single parts, not for a full mix.
            loudness_over_time: loudness of consecutive windows, to see sections

        Args:
            path: The .wav file (16/24/32-bit PCM or float).
            window_seconds: Window length for loudness_over_time.
        """
        return analyze_file(Path(path), window_seconds)
