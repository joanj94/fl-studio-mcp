"""Automation clips: read from saved projects, created in FL Studio.

FL's API can neither read nor create automation. Reading goes through the saved
project (`project/reader.py`): FL's own save or one of its autosaves. Creating
goes through FL's menu and browser (`utils/fl_automation.py`,
`project/preset.py`): the target parameter is set to the value it has, which
makes it FL's "last tweaked" parameter; ADD > "Automation for last tweaked
parameter" makes a clip linked to it; a preset with the wanted points is written
and loaded into that clip's channel. Seen live (FL 2026): the clip then has
those points, keeps its link, and its playlist clip is as long as the last point.
"""

from __future__ import annotations

import logging
import math
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.project import fader
from fl_studio_mcp.project.flp import FlpError, parse
from fl_studio_mcp.project.preset import Point, automation_preset, check_points
from fl_studio_mcp.project.reader import Project, describe_target, read_project
from fl_studio_mcp.tools.browser import Load, send_to_channel
from fl_studio_mcp.utils import fl_browser, fl_channel_rack, fl_playlist, fl_windows, win_focus
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_automation import AutomationError, choose_automation_for_last_tweaked
from fl_studio_mcp.utils.fl_browser import BrowserError
from fl_studio_mcp.utils.fl_playlist import PlaylistError
from fl_studio_mcp.utils.fl_save import SaveError, save_project
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

PROJECTS_FOLDER = "Projects"
# Where the presets go: inside "Project bones", which FL's browser shows.
PRESET_BROWSER_FOLDER = "Project bones/FL-MCP automation"
PRESET_FOLDER = Path("Projects") / "Project bones" / "FL-MCP automation"
FL_TITLE_SUFFIX = " - FL Studio"
# How FL names the automation clip it makes for a parameter: "<owner> - <word>".
PARAMETER_WORDS = {"volume": "Volume", "pan": "Panning"}
DECIMALS = 4
TOUCH_STEP = 0.01  # how far the parameter is moved (and moved back) to count as tweaked
CREATE_ATTEMPTS = 2
NAME_TIMEOUT = 3.0  # seconds FL may take to rename a new automation clip after its target
NAME_POLL_INTERVAL = 0.1


# --- reading ---------------------------------------------------------------------------


def _user_dir() -> Path:
    return get_fl_settings_dir().parent


def _fl_project_title() -> str | None:
    """The open project's file name without ".flp", from FL's window title."""
    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        return None
    title = win_focus._window_title(user32, main).split(FL_TITLE_SUFFIX)[0].strip()
    return title[:-4] if title.lower().endswith(".flp") else None


def find_project_file(projects_dir: Path, title: str | None) -> Path | None:
    """The newest save of the project called `title`: its file or an autosave of it.

    FL autosaves to "<title> (autosaved on ...).flp" in the project's Backup
    folder (and keeps a file a save replaced as "(overwritten on ...)", which is
    older than the save). Without a title, the newest project of all.
    """
    if not projects_dir.is_dir():
        return None
    files = list(projects_dir.rglob("*.flp"))
    if title:
        wanted = re.compile(re.escape(title) + r"( \(autosaved on [^)]*\))?\.flp", re.IGNORECASE)
        files = [f for f in files if wanted.fullmatch(f.name)]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def find_saved_project(projects_dir: Path, title: str) -> Path | None:
    """The open project's own file, "<title>.flp" (not an autosave or backup), newest first."""
    if not projects_dir.is_dir():
        return None
    files = [f for f in projects_dir.rglob("*.flp")
             if f.name.lower() == f"{title}.flp".lower() and "Backup" not in f.parts]
    return max(files, key=lambda f: f.stat().st_mtime, default=None)


def _bars(ticks: int, bar_ticks: int) -> float:
    return round(ticks / bar_ticks + 1, DECIMALS)


def _length(ticks: int, bar_ticks: int) -> float:
    return round(ticks / bar_ticks, DECIMALS)


def _point_dict(point, mixer_volume: bool) -> dict:
    out = {"beat": round(point.beat, DECIMALS), "value": round(point.value, DECIMALS)}
    if point.tension:
        out["tension"] = round(point.tension, DECIMALS)
    if mixer_volume:
        out["db"] = round(fader.position_to_db(min(max(point.value, 0.0), 1.0)), 2)
    return out


def _clip_dict(project: Project, clip, bar_ticks: int) -> dict:
    out = {"track": clip.track, "track_name": project.track_names.get(clip.track, ""),
           "bar": _bars(clip.position, bar_ticks), "length_bars": _length(clip.length, bar_ticks)}
    if clip.pattern is not None:
        out["pattern"] = clip.pattern
    else:
        out["channel"] = clip.channel
        if clip.channel < len(project.channels):
            out["channel_name"] = project.channels[clip.channel]
    return out


def summarize(project: Project, include_clips: bool) -> dict:
    """The project's automation (and optionally all its clips) in bars, values and dB."""
    bar_ticks = project.ppq * 4
    automation = []
    for channel, points in sorted(project.automation.items()):
        targets = [describe_target(rec_id) for rec_id in project.links.get(channel, ())]
        mixer_volume = any(t.get("parameter") == "volume" for t in targets)
        clips = [
            {"arrangement": arrangement.name, "track": clip.track,
             "bar": _bars(clip.position, bar_ticks), "length_bars": _length(clip.length, bar_ticks)}
            for arrangement in project.arrangements for clip in arrangement.clips
            if clip.channel == channel
        ]
        automation.append({
            "channel": channel,
            "name": project.channels[channel] if channel < len(project.channels) else "",
            "targets": targets,
            "points": [_point_dict(p, mixer_volume) for p in points],
            "clips": clips,
        })
    result: dict = {"ppq": project.ppq, "automation": automation}
    if include_clips:
        result["arrangements"] = [
            {"name": arrangement.name,
             "clips": [_clip_dict(project, clip, bar_ticks) for clip in arrangement.clips]}
            for arrangement in project.arrangements
        ]
    return result


# --- creating --------------------------------------------------------------------------


def _point_value(point: dict, kind: str, parameter: str) -> float:
    given = [key for key in ("value", "db", "pan") if key in point]
    if len(given) != 1:
        raise ValueError("Give each point exactly one of: value (0-1), db, pan (-1..1).")
    key = given[0]
    if key == "db":
        if (kind, parameter) != ("mixer track", "volume"):
            raise ValueError("db is only for a mixer track's volume; give value (0-1) instead.")
        return fader.db_to_position(float(point["db"]))
    if key == "pan":
        if parameter != "pan":
            raise ValueError("pan is only for the pan parameter; give value (0-1) instead.")
        pan = float(point["pan"])
        if not -1.0 <= pan <= 1.0:
            raise ValueError(f"pan runs from -1 (left) to 1 (right), not {pan}.")
        return (pan + 1) / 2
    return float(point["value"])


def to_points(points: list[dict], kind: str, parameter: str,
              beats_per_bar: int) -> tuple[int, list[Point]]:
    """(the clip's bar, points in beats from it) of points given at song bars.

    A clip can only be put on a bar line (see `_go_to_bar`): one whose first point
    lies inside a bar starts at that bar, with the first point's value held up to
    it. Raises ValueError.
    """
    if not points:
        raise ValueError("Give the automation's points.")
    try:
        bars = [float(point["bar"]) for point in points]
    except (KeyError, TypeError, ValueError):
        raise ValueError("Every point needs a bar (1 is the song's start).") from None
    if bars[0] < 1:
        raise ValueError("Bars start at 1.")
    start = math.floor(bars[0])
    converted = [
        Point((bar - start) * beats_per_bar, _point_value(point, kind, parameter),
              float(point.get("tension", 0.0)))
        for bar, point in zip(bars, points)
    ]
    if converted[0].beat > 0:
        converted.insert(0, Point(0.0, converted[0].value))
    check_points(converted)
    return start, converted


def _target(mixer_track: int | None, channel: int | str | None, parameter: str) -> dict:
    """What to automate: its kind, index, owner's name and the parameter's current value."""
    if parameter not in PARAMETER_WORDS:
        raise ValueError(f"parameter must be one of: {', '.join(PARAMETER_WORDS)}.")
    if (mixer_track is None) == (channel is None):
        raise ValueError("Give either mixer_track or channel.")
    if mixer_track is not None:
        kind, index = "mixer track", mixer_track
        info = call("mixer.getTrackInfo", {"track": index})
    else:
        kind, index = "channel", resolve_channel(channel)
        info = call("channels.getInfo", {"index": index})
    if "error" in info:
        raise ValueError(info["error"])
    return {"kind": kind, "index": index, "owner": info["name"],
            "value": info[parameter], "parameter": parameter}


def _touch(target: dict) -> None:
    """Move the parameter a little and back: FL then counts it as last tweaked.

    It is really changed (and put back to where it was), so that FL can't take
    the setting as no change at all.
    """
    parameter, value = target["parameter"], target["value"]
    top = 1.0  # volume runs 0..1, pan -1..1: both end at 1
    nudged = value - TOUCH_STEP if value + TOUCH_STEP > top else value + TOUCH_STEP
    if target["kind"] == "mixer track":
        action = "mixer.setTrackVolume" if parameter == "volume" else "mixer.setTrackPan"
        key = {"track": target["index"]}
    else:
        action = "channels.setVolume" if parameter == "volume" else "channels.setPan"
        key = {"index": target["index"]}
    for setting in (nudged, value):
        result = call(action, {**key, parameter: setting})
        if "error" in result:
            raise AutomationError(result["error"])


def _go_to_bar(bar: int) -> None:
    """Click the playlist's time ruler at `bar`: FL puts the clip it makes where the
    playlist was last clicked (`fl_playlist.click_ruler_at`)."""
    bar_ticks = call("playlist.getState")["bar_ticks"]
    try:
        fl_playlist.click_ruler_at(bar, bar_ticks)
    except PlaylistError as e:
        raise AutomationError(
            f"The playlist could not be clicked at bar {bar} ({e}). The song position stays "
            "inside the song, so the automation must start before the song's end."
        ) from e


def _channels() -> list[dict]:
    return call("channels.getAll")["channels"]


def _new_channel(load: Load, before: list[dict]) -> int:
    rack = load.wait_until(_channels, lambda channels: len(channels) > len(before))
    if rack is None:
        raise AutomationError("FL Studio made no automation clip (no new channel appeared).")
    if len(rack) != len(before) + 1:
        raise AutomationError("The channel rack changed in an unexpected way; check it.")
    return len(before)


def _preset_file(channel_name: str, data: bytes) -> tuple[Path, str]:
    """Write the preset; returns where it lies and its browser path."""
    safe = re.sub(r"[^\w\-. ()#]", "_", channel_name).strip() or "Automation"
    folder = _user_dir() / PRESET_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe}.fst"
    path.write_bytes(data)
    return path, f"{PRESET_BROWSER_FOLDER}/{path.name}"


def _make_browser_see(file_name: str) -> None:
    """FL's browser caches folders: reread them unless the file is listed already."""
    try:
        listed = {item.name for item in fl_browser.list_folder(PRESET_BROWSER_FOLDER)}
    except BrowserError:
        listed = set()  # the folder itself is new
    if file_name not in listed:
        fl_browser.reread_structure()


def _settled_name(index: int, expected: str) -> str:
    """The new channel's name once FL has linked it, or what it is after NAME_TIMEOUT.

    Seen live: FL adds the channel as "Auto clip" and renames it after the
    parameter a moment later; read at once, a clip being linked looked unlinked.
    """
    deadline = time.monotonic() + NAME_TIMEOUT
    while True:
        name = _channels()[index]["name"]
        if name.startswith(expected) or time.monotonic() >= deadline:
            return name
        time.sleep(NAME_POLL_INTERVAL)


def _make_linked_clip(load: Load, target: dict) -> tuple[int, str]:
    """Have FL make the clip for the target; returns its channel and name.

    A clip for anything else (an unlinked "Auto clip": the tweak wasn't taken) is
    deleted and FL asked again.
    """
    expected = f"{target['owner']} - {PARAMETER_WORDS[target['parameter']]}"
    made = ""
    for _ in range(CREATE_ATTEMPTS):
        before = _channels()
        _touch(target)
        choose_automation_for_last_tweaked()
        index = _new_channel(load, before)
        made = _settled_name(index, expected)
        if made.startswith(expected):
            return index, made
        fl_channel_rack.delete_channels([index])
    raise AutomationError(
        f"FL made an automation clip for something else ({made!r}, not {expected!r}), "
        f"{CREATE_ATTEMPTS} times; it was removed again."
    )


def create_automation(load: Load, target: dict, start_bar: int, points: list[Point],
                      name: str | None, beats_per_bar: int) -> dict:
    """Make FL create the clip and give it the points; removes it again if that fails."""
    _go_to_bar(start_bar)
    index, made = _make_linked_clip(load, target)
    try:
        path, browser_path = _preset_file(made, automation_preset(points, made))
        _make_browser_see(path.name)
        send_to_channel(load, browser_path, index, keep_name=True)
        if name:
            call("channels.setName", {"index": index, "name": name})
    except Exception:
        fl_channel_rack.delete_channels([index])
        raise
    return {
        "channel": index,
        "name": name or made,
        "target": {key: target[key] for key in ("kind", "index", "owner", "parameter")},
        "start_bar": start_bar,
        "length_bars": round(points[-1].beat / beats_per_bar, DECIMALS),
        "points": len(points),
        "preset": str(path),
        "note": "FL put the clip at its start bar on a playlist track it chose. fl_arrange "
                "clears the playlist, so create automation after arranging.",
    }


def register_automation_tools(mcp: FastMCP) -> None:
    """Register the automation tools with the MCP server."""

    @mcp.tool()
    def fl_save_project() -> str:
        """Save the open project (FL's File > Save) and confirm its file was written.

        The project must have been saved once and live under FL's Projects
        folder, where its file is found by FL's window title (a project without
        a file would make FL open its Save As dialog, left for you to answer).
        In the trial, the "Buy FL Studio" window that comes with saving is
        closed. Windows only.
        """
        try:
            title = _fl_project_title()
            project = find_saved_project(_user_dir() / PROJECTS_FOLDER, title) if title else None
            if project is None:
                return ("Error: the open project's file wasn't found under FL's Projects folder, "
                        "so a save couldn't be confirmed; nothing was done")
            with keyboard_lock:
                shop = save_project(project)
        except SaveError as e:
            return f"Error: {e}"
        except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
            return f"Error: {e}"
        note = " (the trial's \"Buy FL Studio\" window was closed)" if shop else ""
        return f"Project saved to {project}{note}"

    @mcp.tool()
    def fl_read_project(path: str = "", include_clips: bool = False) -> dict:
        """Read automation clips (and playlist clips) from a saved FL Studio project.

        FL's API can't read automation or clips, so this reads the .flp file: by
        default the newest save of the project open in FL, which may be one of FL's
        autosaves, so it shows the project as it was then (save in FL to read the
        latest). Bars assume 4/4.

        Args:
            path: An .flp file; empty for the open project's newest save.
            include_clips: Also list every playlist clip (pattern, audio and
                automation) per arrangement, with its track, bar and length.

        Returns {"file", "saved", "automation": [{"channel", "name", "targets",
        "points", "clips"}], ...}. A target is {"kind": "mixer track", "track",
        "parameter"} where known, always with FL's "rec_id". Points are in beats
        from the clip's start, value 0-1 (and "db" for a mixer volume); "clips"
        tell where the clip lies in the playlist.
        """
        if path:
            file = Path(path).expanduser()
        else:
            file = find_project_file(_user_dir() / PROJECTS_FOLDER, _fl_project_title())
            if file is None:
                return {"error": "No saved project found; save the project in FL or give a path."}
        try:
            project = read_project(parse(file.read_bytes()))
        except (OSError, FlpError) as e:
            return {"error": f"Could not read {file}: {e}"}
        saved = datetime.fromtimestamp(file.stat().st_mtime).isoformat(timespec="seconds")
        return {"file": str(file), "saved": saved, **summarize(project, include_clips)}

    @mcp.tool()
    def fl_create_automation(
        points: list[dict],
        mixer_track: int | None = None,
        channel: int | str | None = None,
        parameter: str = "volume",
        name: str | None = None,
    ) -> dict:
        """Create an automation clip for a mixer track's or a channel's volume or pan.

        FL makes a clip linked to the parameter and puts it into the playlist at
        the first point's bar (on its bar line: a first point inside a bar is held
        from the bar's start), on a playlist track it chooses; the clip is as long
        as its points. fl_arrange clears the whole playlist, so create
        automation after arranging. Windows only.

        Args:
            points: In time order, each {"bar": song bar (1 = start, 33.5 = half
                way into bar 33), and one of "value": 0-1 over the parameter's
                range, "db": a mixer volume in dB (0.8 = 0 dB, up to +5.6),
                "pan": -1 left .. 1 right; optional "tension": -1..1 (0 straight)}.
            mixer_track: The mixer track to automate (0 is the master), or
            channel: a channel index or role.
            parameter: "volume" or "pan".
            name: Name for the automation clip's channel (FL's is "<track> - Volume").

        Returns {"channel", "name", "start_bar", "length_bars", ...} or {"error"}.
        """
        try:
            target = _target(mixer_track, channel, parameter)
            beats_per_bar = call("project.getInfo").get("beats_per_bar", 4)
            start_bar, converted = to_points(points, target["kind"], parameter, beats_per_bar)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        load = Load()
        try:
            with keyboard_lock:
                result = create_automation(load, target, start_bar, converted, name,
                                           beats_per_bar)
        except (AutomationError, BrowserError, fl_channel_rack.ChannelRackError) as e:
            fl_windows.close_popup_menus()
            return {"error": " ".join(filter(None, [str(e), load.note()]))}
        except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
            logger.exception("Creating automation failed unexpectedly")
            fl_windows.close_popup_menus()
            return {"error": f"Creating the automation failed: {e!r}."}
        note = load.note()
        if note:
            result["warning"] = note
        return result
