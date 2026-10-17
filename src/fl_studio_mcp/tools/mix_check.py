"""Checking a mix: what to listen for, gain staging, and the mixer's meters.

`fl_diagnose_mix` renders the mix and its stems and runs the rules in
`audio/mix_doctor.py` (report only). `fl_gain_stage` proposes, and on request
sets, a level per mixer track from the stems' peaks. The meter tools read
`mixer.getPeaks` while FL plays, once or in a background watch
(`utils/peak_watch.py`).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fl_studio_mcp.audio.mix_doctor import diagnose, gain_plan
from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_render import RenderError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
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
