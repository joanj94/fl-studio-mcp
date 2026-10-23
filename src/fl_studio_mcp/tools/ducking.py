"""Ducking a mixer track under the kick, with volume automation instead of a sidechain.

FL's scripting API can route a track to another but can't make that route a
sidechain input, nor pick a compressor's sidechain input (Fruity Limiter's SC
selector is neither a parameter nor a state field that took, seen live; FL's own
assistant tools route with the same `setRouteTo`). The pumping a sidechained
compressor gives can be drawn instead: the track's volume drops on every kick hit
and comes back over `release` beats. That is one automation clip (`automate`, as
fl_create_automation), exact on the grid, and it shows in FL's playlist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.project.preset import MAX_POINTS
from fl_studio_mcp.tools.automation import automate
from fl_studio_mcp.utils.connection import call

if TYPE_CHECKING:
    from fastmcp import FastMCP

DEFAULT_HITS = (0.0, 1.0, 2.0, 3.0)  # a kick on every beat of a 4/4 bar
MAX_DEPTH_DB = 40.0
MIN_DB = -60.0  # what the fader table reaches down to, about


def duck_points(hits: list[float], repeat_beats: float, start_bar: float, bars: float,
                level_db: float, depth_db: float, release_beats: float,
                beats_per_bar: int, tension: float = 0.0) -> list[dict]:
    """Volume points (fl_create_automation's {"bar", "db"}) ducking under the hits.

    `hits` are beats inside one repeat of `repeat_beats`, repeated over `bars` bars
    from `start_bar`. Each hit drops to level - depth at once and rises back to
    level over `release_beats` (cut short where the next hit comes). Raises ValueError.
    """
    if not hits or any(not 0 <= h < repeat_beats for h in hits):
        raise ValueError(f"hits are beats from 0 up to (not including) {repeat_beats:g}")
    if not 0 < depth_db <= MAX_DEPTH_DB:
        raise ValueError(f"depth_db must be more than 0 and at most {MAX_DEPTH_DB:g}")
    if not release_beats > 0:
        raise ValueError("release_beats must be more than 0")
    if not bars > 0 or start_bar < 1:
        raise ValueError("bars must be more than 0 and start_bar at least 1")
    total = bars * beats_per_bar
    times = sorted({repeat * repeat_beats + h
                    for repeat in range(int(total // repeat_beats) + 1) for h in hits
                    if repeat * repeat_beats + h < total})
    ducked = max(level_db - depth_db, MIN_DB)
    points = [{"beat": 0.0, "db": level_db}]
    for i, at in enumerate(times):
        following = times[i + 1] if i + 1 < len(times) else total
        back = min(at + release_beats, following)
        points += [{"beat": at, "db": level_db}, {"beat": at, "db": ducked},
                   {"beat": back, "db": level_db, "tension": tension}]
    if points[-1]["beat"] < total:
        points.append({"beat": total, "db": level_db})
    if len(points) > MAX_POINTS:
        raise ValueError(f"{len(points)} points is more than one clip holds ({MAX_POINTS}): "
                         "duck fewer bars per clip")
    return [{"bar": round(start_bar + p.pop("beat") / beats_per_bar, 6), **p} for p in points]


def register_ducking_tools(mcp: FastMCP) -> None:
    """Register the ducking tool with the MCP server."""

    @mcp.tool()
    def fl_duck_track(
        mixer_track: int,
        start_bar: float = 1,
        bars: float = 16,
        hits: list[float] | None = None,
        repeat_beats: float | None = None,
        depth_db: float = 6.0,
        release_beats: float = 0.5,
        level_db: float | None = None,
        tension: float = 0.0,
    ) -> dict:
        """Make a mixer track duck under the kick: its volume drops on every hit and
        comes back, as a sidechained compressor would (which FL's API can't set up).

        Drawn as one volume automation clip on the track (fl_create_automation), so
        it is exact on the grid and shows in the playlist; the track's fader then
        follows the clip, so set the level with `level_db`, not the fader. Create it
        after fl_arrange (which clears the playlist), one clip per stretch of song
        (a clip holds up to about 160 bars of four-to-the-floor). Windows only.

        Args:
            mixer_track: The track to duck (a bass, a pad, a bus).
            start_bar: Song bar the ducking starts on (1 = the start).
            bars: How many bars it lasts.
            hits: The kick's hits, in beats inside each repeat (default every beat:
                [0, 1, 2, 3]); e.g. [0, 1.5, 2, 3] for a syncopated kick.
            repeat_beats: How long the hit pattern is (default one bar).
            depth_db: How far the volume drops on each hit.
            release_beats: How long it takes to come back (cut short by the next hit).
            level_db: The track's level between hits (default: its fader now).
            tension: Curve of the way back, -1..1 (0 straight).

        Returns fl_create_automation's result plus {"hits"} or {"error"}.
        """
        info = call("project.getInfo")
        if "error" in info:
            return info
        beats_per_bar = info.get("beats_per_bar") or 4
        if level_db is None:
            track = call("mixer.getTrackInfo", {"track": mixer_track})
            if "error" in track:
                return track
            level_db = round(float(track["volume_db"]), 2)
        try:
            points = duck_points(list(hits or DEFAULT_HITS), repeat_beats or beats_per_bar,
                                 start_bar, bars, level_db, depth_db, release_beats,
                                 beats_per_bar, tension)
        except ValueError as e:
            return {"error": str(e)}
        result = automate(points, mixer_track=mixer_track, parameter="volume",
                          name=f"Duck {mixer_track}")
        if "error" not in result:
            result["hits"] = sum(1 for p in points if p["db"] < level_db)
        return result
