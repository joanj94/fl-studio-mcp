"""Snapshots of the mix, and a report of what the project no longer uses.

A snapshot keeps every mixer track in use (level, pan, mute, solo, name,
routes, its effects' on/off, mix and parameters) and every channel (name,
level, pan, mute, mixer track), as JSON in the controller's folder. FL's undo
history is per step and can't be named; a snapshot is a whole mix to go back
to. Reading and putting back are one controller command each
(`project.getSnapshot`, `project.applyState`), and only what differs is changed.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fl_studio_mcp.project.flp import FlpError, parse
from fl_studio_mcp.project.reader import read_project
from fl_studio_mcp.tools.automation import newest_project_file
from fl_studio_mcp.tools.piano_roll import read_notes
from fl_studio_mcp.utils import connection
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.paths import atomic_write_json, get_hardware_dir

if TYPE_CHECKING:
    from fastmcp import FastMCP

SNAPSHOT_FOLDER = "snapshots"
SNAPSHOT_TIMEOUT = 15.0  # an effect's parameters are read or set one by one inside FL
NAME_PATTERN = re.compile(r"[\w .-]{1,64}")


def snapshots_dir() -> Path:
    return get_hardware_dir() / SNAPSHOT_FOLDER


def _snapshot_path(name: str) -> Path:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or name.strip(". ") != name:
        raise ValueError("A snapshot name is 1-64 letters, digits, spaces, '.', '-' or '_', "
                         f"not starting or ending with a space or dot; got {name!r}")
    return snapshots_dir() / f"{name}.json"


def _long_call(action: str, params: dict | None = None) -> dict:
    """call() with room for a command that walks every effect's parameters."""
    try:
        conn = connection.get_connection()
        result = conn.send_command(action, params or {}, timeout=SNAPSHOT_TIMEOUT)
    except RuntimeError as e:
        return {"error": str(e)}
    if not result.get("success", False):
        return {"error": result.get("error") or f"FL Studio could not run {action}"}
    return {k: v for k, v in result.items() if k not in ("success", "id")}


def match_channels(saved: list[dict], rack: list[dict]) -> tuple[list[dict], list[str]]:
    """The saved channels with the index they have now, and the ones not found.

    A channel is found by its name when one channel has it; otherwise, when the
    rack is as long as it was, by its place.
    """
    by_name: dict[str, list[int]] = {}
    for channel in rack:
        by_name.setdefault(channel["name"], []).append(channel["index"])
    same_rack = len(rack) == len(saved)
    matched, missing = [], []
    for channel in saved:
        found = by_name.get(channel.get("name"), [])
        if len(found) == 1:
            matched.append({**channel, "index": found[0]})
        elif same_rack:
            matched.append(channel)
        else:
            missing.append(channel.get("name") or f"channel {channel.get('index')}")
    return matched, missing


def _unused_tracks(tracks: list[dict]) -> list[dict]:
    fed = {d for t in tracks for d in t.get("routes", [])}
    return [{"track": t["index"], "name": t["name"]} for t in tracks
            if t["index"] > 0 and not t.get("channels") and t["index"] not in fed]


def _from_save(info: dict) -> dict:
    """Patterns and automation channels the newest save has no clip of."""
    file = newest_project_file()
    if file is None:
        return {"error": "No saved project found: save it in FL to check the playlist"}
    try:
        project = read_project(parse(file.read_bytes()))
    except (OSError, FlpError) as e:
        return {"error": f"Could not read {file}: {e}"}
    clips = [c for a in project.arrangements for c in a.clips]
    placed = {c.pattern for c in clips if c.pattern is not None}
    clip_channels = {c.channel for c in clips if c.channel is not None}
    return {
        "file": str(file),
        "saved": datetime.fromtimestamp(file.stat().st_mtime).isoformat(timespec="seconds"),
        "pattern_notes": project.pattern_notes,
        "patterns_not_in_playlist": [
            {"pattern": p["index"], "name": p.get("name", "")}
            for p in info.get("patterns", []) if p["index"] not in placed
        ],
        "automation_without_clip": [
            {"channel": c["index"], "name": c["name"]}
            for c in info.get("channels", [])
            if c.get("is_automation") and c["index"] not in clip_channels
        ],
    }


def _silent_channels(info: dict) -> list[dict]:
    """Channels with no note in any used pattern (reads every piano roll)."""
    patterns = [p["index"] for p in info.get("patterns", []) if p.get("length_beats")]
    silent = []
    try:
        for channel in info.get("channels", []):
            if channel.get("is_automation"):
                continue
            if not any(read_notes(channel["index"], p) for p in patterns):
                silent.append({"channel": channel["index"], "name": channel["name"]})
    finally:  # reading leaves another pattern active
        if info.get("current_pattern"):
            call("patterns.select", {"index": info["current_pattern"]})
    return silent


def _empty_patterns(info: dict, pattern_notes: dict[int, int] | None) -> list[dict]:
    """Patterns FL lists (named or once used) that hold no notes.

    Seen live: FL reports a bar's length for a pattern without notes, so the length
    can't tell; the save's note count can. A pattern the save doesn't know (made
    since) counts as empty only when its length is 0.
    """
    empty = []
    for p in info.get("patterns", []):
        notes = (pattern_notes or {}).get(p["index"])
        if notes == 0 or (notes is None and not p.get("length_beats")):
            empty.append({"pattern": p["index"], "name": p.get("name", "")})
    return empty


def find_unused(from_save: bool = True, check_notes: bool = False) -> dict:
    """fl_find_unused's report (also the plan of fl_clean_unused)."""
    info = call("project.getInfo")
    if "error" in info:
        return info
    tracks = call("mixer.getAllTracks")
    if "error" in tracks:
        return tracks
    result: dict[str, Any] = {"mixer_tracks": _unused_tracks(tracks.get("tracks", []))}
    pattern_notes = None
    if from_save:
        saved = _from_save(info)
        if "error" in saved:
            result["save"] = saved
        else:
            result["save"] = {"file": saved.pop("file"), "saved": saved.pop("saved")}
            pattern_notes = saved.pop("pattern_notes")
            result.update(saved)
    result["empty_patterns"] = _empty_patterns(info, pattern_notes)
    if check_notes:
        try:
            result["silent_channels"] = _silent_channels(info)
        except (ValueError, RuntimeError) as e:
            result["silent_channels_error"] = str(e)
    return result


def _clear_track(track: int) -> list[str]:
    """Delete a mixer track's effects (last slot first) and reset its name; problems found."""
    from fl_studio_mcp.tools.browser import delete_effect

    effects = call("mixer.getTrackEffects", {"track": track})
    if "error" in effects:
        return [f"track {track}: {effects['error']}"]
    problems = []
    for effect in sorted(effects.get("effects", []), key=lambda e: -e["slot"]):
        deleted = delete_effect(track, effect["slot"])
        if "error" in deleted:
            problems.append(f"track {track} slot {effect['slot']}: {deleted['error']}")
    renamed = call("mixer.setTrackName", {"track": track, "name": ""})
    if "error" in renamed:
        problems.append(f"track {track} name: {renamed['error']}")
    return problems


def _clean_automation(report: dict) -> tuple[list[str], str | None]:
    """(deleted channel names, problem) for automation channels without a clip."""
    if not report.get("automation_without_clip"):
        return [], None
    from fl_studio_mcp.tools.channel_setup import delete_channels

    deleted = delete_channels([c["channel"] for c in report["automation_without_clip"]])
    if "error" in deleted:
        return [], f"automation channels: {deleted['error']}"
    return deleted["deleted"], None


def clean_unused(mixer_tracks: bool = True, empty_patterns: bool = True,
                 automation: bool = True) -> dict:
    """fl_clean_unused's work: what find_unused reports, removed.

    The playlist and the notes are read from the newest save, so they are only
    used when nothing changed since: a clip or note added since would be missed.
    """
    history = call("project.getUndoHistory")
    if "error" in history:
        return history
    use_save = not history.get("unsaved_changes")
    report = find_unused(from_save=use_save)
    if "error" in report:
        return report
    cleaned: dict[str, list] = {"mixer_tracks": [], "patterns": [], "channels": []}
    problems: list[str] = []
    left: dict[str, Any] = {}
    if empty_patterns:
        for pattern in report["empty_patterns"]:
            renamed = call("patterns.rename", {"index": pattern["pattern"], "name": ""})
            if "error" in renamed:
                problems.append(f"pattern {pattern['pattern']}: {renamed['error']}")
            else:
                cleaned["patterns"].append(pattern["pattern"])
    if mixer_tracks:
        for track in report["mixer_tracks"]:
            found = _clear_track(track["track"])
            problems += found
            if not found:
                cleaned["mixer_tracks"].append(track["track"])
    if not use_save:
        left["save"] = ("The project has changes since its last save: save it "
                        "(fl_save_project), then clean again. Automation channels and "
                        "empty patterns are found from the save.")
    elif "error" in report["save"]:
        left["save"] = report["save"]["error"]
    elif automation:
        cleaned["channels"], problem = _clean_automation(report)
        if problem:
            problems.append(problem)
    spares = [p for p in report.get("patterns_not_in_playlist", [])
              if p["pattern"] not in cleaned["patterns"]]
    if spares:
        left["patterns_not_in_playlist"] = spares
    result: dict[str, Any] = {"cleaned": cleaned}
    if left:
        result["left"] = left
    if problems:
        result["problems"] = problems
    return result


def register_snapshot_tools(mcp: FastMCP) -> None:
    """Register the snapshot and clean-up report tools with the MCP server."""

    @mcp.tool()
    def fl_take_snapshot(name: str, params: bool = True) -> dict:
        """Keep the mix as it is now under a name, to come back to with fl_restore_snapshot.

        Keeps every mixer track in use (level, pan, mute, solo, name, routes, and
        each effect's on/off, mix and parameters) and every channel (name, level,
        pan, mute, mixer track). Not kept: notes, patterns, the playlist,
        automation, which plugins are loaded. A snapshot of the same name is
        replaced.

        Args:
            name: e.g. "before eq".
            params: Also keep the effects' parameters (up to 1024 per effect).

        Returns {"name", "path", "tracks", "channels", "effects"} or {"error"}.
        """
        try:
            path = _snapshot_path(name)
        except ValueError as e:
            return {"error": str(e)}
        state = _long_call("project.getSnapshot", {"params": params})
        if "error" in state:
            return state
        taken = datetime.now().isoformat(timespec="seconds")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, {"name": name, "taken": taken, **state})
        except OSError as e:
            return {"error": f"Could not write {path}: {e}"}
        return {"name": name, "path": str(path), "tracks": len(state["tracks"]),
                "channels": len(state["channels"]),
                "effects": sum(len(t.get("effects", [])) for t in state["tracks"])}

    @mcp.tool()
    def fl_restore_snapshot(name: str, mixer: bool = True, channels: bool = True) -> dict:
        """Put the mix back as a snapshot kept it; only what differs is changed.

        Channels are found by name (or by place, if the rack is as long as it
        was); an effect is only set back if its slot still holds the same plugin.
        Mixer tracks added since are left as they are.

        Args:
            name: The snapshot (fl_list_snapshots).
            mixer: Put back the mixer tracks and their effects.
            channels: Put back the channels.

        Returns {"name", "changed", "skipped": [...]} or {"error"}.
        """
        try:
            snapshot = json.loads(_snapshot_path(name).read_text(encoding="utf-8"))
        except ValueError as e:
            return {"error": str(e)}
        except OSError:
            return {"error": f"There is no snapshot {name!r}; fl_list_snapshots lists them"}
        skipped: list[str] = []
        rack: list[dict] = []
        if channels:
            current = call("channels.getAll")
            if "error" in current:
                return current
            rack, missing = match_channels(snapshot.get("channels", []), current["channels"])
            skipped += [f"channel {n!r} isn't in the rack any more" for n in missing]
        applied = _long_call("project.applyState", {
            "tracks": snapshot.get("tracks", []) if mixer else [], "channels": rack,
        })
        if "error" in applied:
            return applied
        return {"name": name, "changed": applied["changed"],
                "skipped": skipped + applied.get("skipped", [])}

    @mcp.tool()
    def fl_list_snapshots() -> dict:
        """The snapshots kept, newest first: [{"name", "taken", "tracks", "channels"}]."""
        found = []
        for path in snapshots_dir().glob("*.json") if snapshots_dir().is_dir() else []:
            try:
                data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            found.append({"name": data.get("name", path.stem), "taken": data.get("taken"),
                          "tracks": len(data.get("tracks", [])),
                          "channels": len(data.get("channels", []))})
        return {"snapshots": sorted(found, key=lambda s: s["taken"] or "", reverse=True)}

    @mcp.tool()
    def fl_find_unused(from_save: bool = True, check_notes: bool = False) -> dict:
        """List what the project no longer uses, to clean it up. Changes nothing.

        - mixer tracks in use (named or with effects) that nothing plays into
        - patterns that are named but hold no notes
        - with from_save: patterns without a clip in the playlist, and automation
          channels without a clip, as of the newest save (save first for now)
        - with check_notes: channels without a note in any pattern (reads every
          channel's piano roll in every pattern: about half a second each)

        Returns {"mixer_tracks", "empty_patterns", "patterns_not_in_playlist"?,
        "automation_without_clip"?, "silent_channels"?, "save"?} or {"error"}.
        """
        return find_unused(from_save, check_notes)

    @mcp.tool()
    def fl_clean_unused(mixer_tracks: bool = True, empty_patterns: bool = True,
                        automation: bool = True) -> dict:
        """Remove what the project no longer uses (fl_find_unused shows it first).

        - mixer_tracks: tracks that nothing plays into lose their effects and name
        - empty_patterns: named patterns without notes get their name reset, which
          makes them unused again
        - automation: automation channels without a clip in the playlist are deleted.
          The playlist can only be read from a save, so this is skipped while the
          project has unsaved changes (save with fl_save_project first).

        Patterns with notes but no clip are never touched (they may be spares):
        they come back under "left". Each step can be undone with fl_undo.
        Windows only (effects and channels go through FL's menus or own tools).

        Returns {"cleaned": {"mixer_tracks", "patterns", "channels"}, "left"?,
        "problems"?} or {"error"}.
        """
        return clean_unused(mixer_tracks, empty_patterns, automation)
