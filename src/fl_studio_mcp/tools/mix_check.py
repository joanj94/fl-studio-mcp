"""Checking a mix: what to listen for, gain staging, and the mixer's meters.

`fl_diagnose_mix` renders the mix and its stems and runs the rules in
`audio/mix_doctor.py` (report only). `fl_gain_stage` proposes, and on request
sets, a level per mixer track from the stems' peaks. The meter tools read
`mixer.getPeaks` while FL plays, once or in a background watch
(`utils/peak_watch.py`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.audio.loudness_target import next_gain
from fl_studio_mcp.audio.mix_doctor import diagnose, gain_plan
from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools.mixing import FLAT_DB, LEVEL_TYPES, _bands, find_or_add_plugin
from fl_studio_mcp.tools.plugins import param_by_name, set_param_by_value
from fl_studio_mcp.utils.connection import call, get_connection
from fl_studio_mcp.utils.fl_render import RenderError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.param_values import parse_display
from fl_studio_mcp.utils.peak_watch import (
    DEFAULT_INTERVAL,
    DEFAULT_OVER_DBFS,
    MAX_SECONDS,
    PeakWatch,
    to_dbfs,
)
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

MAX_LEVEL_SECONDS = 10.0
MASTER = 0
LIMITER = "Fruity Limiter"
MAX_MASTER_RENDERS = 8
MAX_LIMITER_GAIN_DB = 18.0  # seen live: Fruity Limiter's Gain shows -INF to 18.1 dB
watch = PeakWatch(call)


def _mixer_and_rack() -> tuple[list[dict], list[dict]]:
    tracks = audio_tools._fl("mixer.getAllTracks").get("tracks", [])
    rack = audio_tools._fl("channels.getAll").get("channels", [])
    return tracks, rack


def _render_mix(timeout: float) -> dict:
    """The full mix's measurements (through the master's effects); the file is deleted."""
    path = audio_tools._new_render_path("mix")
    audio_tools.render_audio(path, timeout)
    try:
        return audio_tools.analyze_file(path, audio_tools.DEFAULT_WINDOW_SECONDS, tempo=False)
    finally:
        path.unlink(missing_ok=True)


def _channels(channels: list[int | str] | None) -> list[int] | None:
    return None if channels is None else [resolve_channel(c) for c in channels]


def _tracks_to_watch(tracks: list[int] | None) -> tuple[list[int], dict[int, str]]:
    listed = call("mixer.getAllTracks")
    if "error" in listed:
        raise ValueError(listed["error"])
    names = {t["index"]: t["name"] for t in listed.get("tracks", [])}
    return (sorted(names) if tracks is None else list(tracks)), names


def _limiter_gain(conn, slot: int) -> tuple[int, float]:
    """(Gain parameter's index, the dB it shows) of the master's limiter."""
    index, _ = param_by_name(conn, MASTER, slot, "Gain")
    shown = conn.send_command("plugins.getParamValue", {
        "param_index": index, "plugin_index": MASTER, "slot_index": slot})
    parsed = parse_display(str(shown.get("value_string", "")))
    if not shown.get("success", False) or parsed is None:
        raise ValueError("The limiter's gain could not be read")
    return index, parsed[1]


def master_to_loudness(target_lufs: float, ceiling_db: float, tolerance: float,
                       max_renders: int, timeout: float) -> dict:
    """Set the master limiter's ceiling, then its gain render by render. Raises ValueError."""
    conn = get_connection()
    slot, added = find_or_add_plugin(MASTER, LIMITER)
    ceiling = set_param_by_value(conn, MASTER, slot, param_by_name(
        conn, MASTER, slot, "Limiter ceiling")[0], f"{ceiling_db:g}dB")["shows"]
    gain_index, gain = _limiter_gain(conn, slot)
    try:
        return _limit_to_target(conn, slot, gain_index, gain, target_lufs, tolerance,
                                max_renders, timeout) | {"slot": slot, "added": added,
                                                         "ceiling": ceiling}
    except (RenderError, ValueError) as e:
        state = f"the limiter in the master's slot {slot}" + (" (added by this run)"
                                                              if added else "")
        raise ValueError(
            f"{e} ({state} keeps its last gain; fl_get_plugin_params shows it)") from e


def _limit_to_target(conn, slot: int, gain_index: int, gain: float, target_lufs: float,
                     tolerance: float, max_renders: int, timeout: float) -> dict:
    history: list[tuple[float, float]] = []
    renders = []
    while True:
        mix = _render_mix(timeout)
        if "error" in mix:
            raise ValueError(mix["error"])
        lufs = mix.get("loudness_lufs")
        if lufs is None:
            raise ValueError("The render is silent: nothing plays in song mode")
        history.append((gain, lufs))
        renders.append({"gain_db": gain, "loudness_lufs": lufs, "peak_dbfs": mix["peak_dbfs"]})
        if abs(lufs - target_lufs) <= tolerance or len(renders) >= max_renders:
            break
        wanted = min(MAX_LIMITER_GAIN_DB, next_gain(history, target_lufs))
        if wanted <= gain + 0.05 and gain >= MAX_LIMITER_GAIN_DB - 0.05:
            break  # the gain is at its top already
        shows = set_param_by_value(conn, MASTER, slot, gain_index, f"{wanted:g}dB")["shows"]
        parsed = parse_display(shows)
        gain = parsed[1] if parsed else wanted
    reached = abs(renders[-1]["loudness_lufs"] - target_lufs) <= tolerance
    result = {"reached": reached, "renders": renders}
    at_top = gain >= MAX_LIMITER_GAIN_DB - 0.5
    if not reached and renders[-1]["loudness_lufs"] < target_lufs and at_top:
        result["note"] = (f"The limiter's gain is at its top (+{MAX_LIMITER_GAIN_DB:g} dB): "
                          "raise the mixer tracks going into the master, then run this again.")
    return result


EQ = "Fruity Parametric EQ 2"
EQ_WIDTH = "40%"  # about an octave, like the EQ intents


def _render_to_file(timeout: float) -> Path:
    path = audio_tools._new_render_path("match")
    audio_tools.render_audio(path, timeout)
    return path


def set_eq_bands(track: int, bands: list[dict]) -> dict:
    """Put EQ bands ({"hz", "gain_db", "type"}) on free bands of the track's Parametric EQ 2.

    A free band is one that only changes a level and sits at 0 dB. Raises ValueError.
    """
    conn = get_connection()
    slot, added = find_or_add_plugin(track, EQ)
    free = [band for band, state in sorted(_bands(conn, track, slot).items())
            if state.get("type", "").lower() in LEVEL_TYPES
            and abs((parse_display(state.get("level", "")) or ("", 99.0))[1]) < FLAT_DB]
    if len(free) < len(bands):
        raise ValueError(f"The EQ in slot {slot} has {len(free)} free bands, "
                         f"{len(bands)} are needed: clear some or ask for fewer")
    applied = []
    for band, move in zip(free, bands):
        values = {"type": move["type"], "freq": f"{move['hz']}Hz",
                  "level": f"{move['gain_db']:g}dB"}
        if move["type"] == "Peaking":
            values["width"] = EQ_WIDTH
        shown = {}
        for what, value in values.items():
            index, _ = param_by_name(conn, track, slot, f"Band {band} {what}")
            shown[what] = set_param_by_value(conn, track, slot, index, value)["shows"]
        applied.append({"band": band, **shown})
    return {"track": track, "slot": slot, "added": added, "set": applied}


def register_mix_check_tools(mcp: FastMCP) -> None:
    """Register the mix checking and metering tools with the MCP server."""

    @mcp.tool()
    def fl_diagnose_mix(
        channels: list[int | str] | None = None,
        render_mix: bool = True,
        timeout: float = audio_tools.DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Render the mix and every part, and list what is worth checking. Changes nothing.

        Looks for clipping and a peak too close to full scale in the mix (through
        the master's effects); parts that peak over 0 dBFS before the master's
        effects; parts buried far under the loudest; parts other than the bass
        parts that carry a lot of low end, or many parts sharing it; two loud
        parts centred on the same band at the same level (masking); channels
        playing straight into the master; related tracks (sharing a word in
        their names) that could share a bus. Only clipping is a "problem"; the
        rest are "checks" that may well be meant. Takes one render plus one per
        channel. Windows only.

        Args:
            channels: Channels (index or role) to render as stems; default: every
                channel that isn't muted.
            render_mix: Also render the full mix (for its clipping and headroom).
            timeout: Seconds to wait for each render.

        Returns {"findings": [{"severity", "kind", "parts", "message"}], "mix":
        {"loudness_lufs", "peak_dbfs", "clipped_samples"}, "stems": [...]} or {"error"}.
        """
        try:
            indexes = _channels(channels)
            with keyboard_lock:
                mix = _render_mix(timeout) if render_mix else None
                if mix and "error" in mix:
                    return mix
                stems = audio_tools.render_stems(indexes, timeout, False, master_effects=False)
                tracks, rack = _mixer_and_rack()
        except (RenderError, ValueError, RuntimeError) as e:
            return {"error": str(e)}
        result = {
            "findings": diagnose(mix, stems["stems"], tracks, rack, master_effects=False),
            "stems": stems["stems"],
        }
        if mix:
            result["mix"] = {k: mix.get(k) for k in ("loudness_lufs", "peak_dbfs",
                                                     "clipped_samples")}
        if "warning" in stems:
            result["warning"] = stems["warning"]
        return result

    @mcp.tool()
    def fl_gain_stage(
        target_peak_dbfs: float = -6.0,
        apply: bool = False,
        channels: list[int | str] | None = None,
        timeout: float = audio_tools.DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Level every mixer track so its part peaks at the same level (gain staging).

        Renders each channel on its own with the master's effects off and gives
        each mixer track the change in dB that brings its loudest channel's peak
        to `target_peak_dbfs`. With apply=true the faders are set. This evens out
        the levels going into the master; it isn't a balance (a part meant to sit
        back is brought up too), so balance by ear or by loudness afterwards.
        Takes one render per channel. Windows only.

        Args:
            target_peak_dbfs: The peak every part should have (-6 leaves room for
                them to add up).
            apply: Set the faders; default: only propose.
            channels: Channels (index or role) to render; default: every channel
                that isn't muted.
            timeout: Seconds to wait for each render.

        Returns {"tracks": [{"track", "name", "peak_dbfs", "loudest_channel",
        "change_db", "volume_db", "new_volume_db", "note"?}], "applied"} or {"error"}.
        """
        if not -60 <= target_peak_dbfs <= 0:
            return {"error": f"target_peak_dbfs must be -60 to 0, got {target_peak_dbfs}"}
        try:
            indexes = _channels(channels)
            with keyboard_lock:
                stems = audio_tools.render_stems(indexes, timeout, False, master_effects=False)
                tracks, rack = _mixer_and_rack()
        except (RenderError, ValueError, RuntimeError) as e:
            return {"error": str(e)}
        plan = gain_plan(stems["stems"], rack, tracks, target_peak_dbfs)
        failed = []
        if apply:
            for entry in plan:
                if "new_volume_db" not in entry:
                    continue
                done = call("mixer.setTrackVolumeDb",
                            {"track": entry["track"], "db": entry["new_volume_db"]})
                if "error" in done:
                    failed.append(f"track {entry['track']}: {done['error']}")
                else:
                    entry["new_volume_db"] = round(done.get("volume_db", entry["new_volume_db"]),
                                                   1)
        result = {"tracks": plan, "applied": apply and not failed}
        if failed:
            result["error"] = "Some faders could not be set: " + "; ".join(failed)
        return result

    @mcp.tool()
    def fl_master_to_loudness(
        target_lufs: float = -14.0,
        ceiling_db: float = -1.0,
        tolerance: float = 0.5,
        max_renders: int = 5,
        timeout: float = audio_tools.DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Bring the whole song to a loudness (integrated LUFS) with a limiter on the master.

        Uses the master's Fruity Limiter (adds one at the end of the chain if it
        has none), sets its ceiling, then renders and adjusts the limiter's gain
        until the song measures within `tolerance` of the target. The more gain,
        the harder the limiter works: compare the renders' peak and the crest
        factor of fl_render to judge what it costs. Renders what FL plays (song
        mode for the whole song). Takes 2-5 renders. Windows only.

        Args:
            target_lufs: Integrated loudness to reach (-14 is a common streaming
                level, louder club masters sit around -9 to -6).
            ceiling_db: The limiter's ceiling (the highest peak), -12 to 0.
            tolerance: How close the loudness must come, in LU.
            max_renders: Renders to try at most (1-8).
            timeout: Seconds to wait for each render.

        Returns {"slot", "added", "ceiling", "reached", "renders": [{"gain_db",
        "loudness_lufs", "peak_dbfs"}]} or {"error"}.
        """
        if not -40 <= target_lufs <= 0:
            return {"error": f"target_lufs must be -40 to 0, got {target_lufs}"}
        if not -12 <= ceiling_db <= 0:
            return {"error": f"ceiling_db must be -12 to 0, got {ceiling_db}"}
        if isinstance(max_renders, bool) or not 1 <= max_renders <= MAX_MASTER_RENDERS:
            return {"error": f"max_renders must be 1-{MAX_MASTER_RENDERS}, got {max_renders!r}"}
        if not 0.1 <= tolerance <= 3:
            return {"error": f"tolerance must be 0.1-3 LU, got {tolerance}"}
        try:
            with keyboard_lock:
                return master_to_loudness(target_lufs, ceiling_db, tolerance, max_renders,
                                          timeout)
        except (RenderError, ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_match_eq(
        reference: str,
        apply: bool = False,
        track: int = MASTER,
        max_bands: int = 4,
        strength: float = 0.7,
        timeout: float = audio_tools.DEFAULT_RENDER_TIMEOUT,
    ) -> dict:
        """Render the song and propose EQ moves toward a reference track's tonal balance.

        Like audio_match_eq on a fresh render of what FL plays (song mode for the
        whole song). With apply=true the moves go onto free bands of a Fruity
        Parametric EQ 2 on `track` (one is added if the track has none); the
        master by default. Render and compare again to check, and judge by ear:
        a reference is another song. Windows only.

        Args:
            reference: The audio file to match (any format audio_analyze reads).
            apply: Set the EQ; default: only propose.
            track: Mixer track for the EQ (0 = master).
            max_bands: EQ bands at most (1-7).
            strength: Share of each difference to correct (0-1].
            timeout: Seconds to wait for the render.

        Returns {"bands", "curve", "applied"?: {"track", "slot", "added", "set"}}
        or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.eq_match import file_spectrum, match_eq
        except ImportError:
            return {"error": audio_tools.MISSING_NUMPY}
        try:
            wanted = file_spectrum(Path(reference))
            with keyboard_lock:
                path = _render_to_file(timeout)
                try:
                    result = match_eq(file_spectrum(path), wanted, max_bands, strength)
                finally:
                    path.unlink(missing_ok=True)
                if apply and result["bands"]:
                    result["applied"] = set_eq_bands(track, result["bands"])
        except MemoryError:
            return {"error": "Not enough memory to compare files this long"}
        except (RenderError, ValueError, RuntimeError, OSError) as e:
            return {"error": str(e)}
        return result

    @mcp.tool()
    def fl_get_track_level(tracks: list[int] | None = None, seconds: float = 1.0) -> dict:
        """The mixer meters' highest peak over the next few seconds, while FL plays.

        Start playback first (fl_play). For a whole song, use fl_watch_peaks_start.

        Args:
            tracks: Mixer tracks (0 = master); default: the tracks in use.
            seconds: How long to watch (up to 10).

        Returns {"seconds", "polls", "tracks": [{"track", "name", "peak_dbfs"}]} or {"error"}.
        """
        if not 0 < seconds <= MAX_LEVEL_SECONDS:
            return {"error": f"seconds must be more than 0 and at most {MAX_LEVEL_SECONDS:.0f}"}
        try:
            if not call("transport.getStatus").get("is_playing"):
                return {"error": "FL isn't playing: the meters show nothing. Start it with "
                                 "fl_play."}
            watched, names = _tracks_to_watch(tracks)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        highest = dict.fromkeys(watched, 0.0)
        polls = 0
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                result = call("mixer.getPeaks", {"tracks": watched})
            except RuntimeError as e:
                return {"error": str(e)}
            if "error" in result:
                return result
            polls += 1
            for entry in result.get("peaks", []):
                highest[entry["track"]] = max(highest[entry["track"]], entry["peak"] or 0)
        return {"seconds": seconds, "polls": polls, "tracks": [
            {"track": t, "name": names.get(t, f"Insert {t}"), "peak_dbfs": to_dbfs(p)}
            for t, p in highest.items()
        ]}

    @mcp.tool()
    def fl_watch_peaks_start(
        tracks: list[int] | None = None,
        over_dbfs: float = DEFAULT_OVER_DBFS,
        interval: float = DEFAULT_INTERVAL,
        max_seconds: float = MAX_SECONDS,
    ) -> dict:
        """Watch the mixer meters in the background, e.g. while the song plays through.

        Keeps each track's highest peak and counts the readings at or above
        `over_dbfs`. Start playback (fl_play), let it run, then read it with
        fl_watch_peaks_status or end it with fl_watch_peaks_stop. Other tools
        wait a little longer while it runs. It stops by itself after max_seconds.

        Args:
            tracks: Mixer tracks (0 = master); default: the tracks in use.
            over_dbfs: The level a reading counts as "over" at.
            interval: Seconds between readings (0.02 or more).
            max_seconds: When it stops by itself (up to 1800).

        Returns {"watching": [tracks]} or {"error"}.
        """
        try:
            watched, _ = _tracks_to_watch(tracks)
            watch.start(watched, interval, over_dbfs, max_seconds)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        return {"watching": watched}

    @mcp.tool()
    def fl_watch_peaks_status() -> dict:
        """What the peak watch has seen so far (it keeps running).

        Returns {"running", "seconds", "polls", "over_dbfs", "tracks": [{"track",
        "peak_dbfs", "overs", "first_over_seconds"}], "error"?} or {"error"}.
        """
        return watch.status() or {"error": "No peak watch has been started"}

    @mcp.tool()
    def fl_watch_peaks_stop() -> dict:
        """End the peak watch and return what it saw (as fl_watch_peaks_status)."""
        if not watch.status():
            return {"error": "No peak watch has been started"}
        return watch.stop()
