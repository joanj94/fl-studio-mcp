"""Group tools: mute, solo or colour several mixer tracks or channels in one call.

A group is matched like a role (utils/roles.py), by whole words, but may match
many: a mixer track belongs to it when its own name or the name of a channel
routed to it holds one of the group's words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.colors import parse_color, rgb
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.roles import get_channels, matches_group

if TYPE_CHECKING:
    from fastmcp import FastMCP


class NoMatch(ValueError):
    """Nothing in the project matches a group."""


def _mixer_tracks() -> list[dict]:
    result = call("mixer.getAllTracks")
    if "error" in result:
        raise ValueError(result["error"])
    return result.get("tracks", [])


def resolve_tracks(group: str | None, tracks: list[int] | None) -> list[int]:
    """Insert track indexes for a group and/or explicit tracks. Raises ValueError."""
    if group is None and not tracks:
        raise ValueError("Give a group (e.g. \"drums\" or \"kick, clap\") or a list of tracks")
    found = set(tracks or [])
    if group is not None:
        matched = {
            t["index"] for t in _mixer_tracks()
            if t["index"] != 0 and matches_group(group, [t.get("name", "")] + t.get("channels", []))
        }
        if not matched:
            raise NoMatch(f"No mixer track or channel routed to one matches {group!r}")
        found |= matched
    return sorted(found)


def resolve_channels(group: str | None, indexes: list[int] | None) -> list[int]:
    """Channel indexes for a group and/or explicit indexes. Raises ValueError."""
    found = set(indexes or [])
    if group is not None:
        matched = {c["index"] for c in get_channels() if matches_group(group, [c.get("name", "")])}
        if not matched:
            raise NoMatch(f"No channel matches {group!r}")
        found |= matched
    return sorted(found)


def _group_or_none(resolve, group: str | None) -> list[int]:
    """What `resolve` finds for the group alone; nothing for no group or no match."""
    if group is None:
        return []
    try:
        return resolve(group, None)
    except NoMatch:
        return []


def register_group_tools(mcp: FastMCP) -> None:
    """Register group tools with the MCP server."""

    @mcp.tool()
    def fl_mute_tracks(group: str | None = None, tracks: list[int] | None = None,
                       muted: bool = True) -> dict:
        """Mute (or unmute) several mixer tracks at once; the others stay as they are.

        Args:
            group: Words matched in track names and in the names of the channels
                routed to them, e.g. "drums" or "kick, clap, hat".
            tracks: Insert track indexes, alone or added to the group's.
            muted: True mutes, False unmutes.

        Returns {"tracks": [...], "muted": bool} or {"error"}.
        """
        try:
            found = resolve_tracks(group, tracks)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        for track in found:
            result = call("mixer.muteTrack", {"track": track, "muted": muted})
            if "error" in result:
                return {"error": f"Track {track}: {result['error']}"}
        return {"tracks": found, "muted": muted}

    @mcp.tool()
    def fl_solo_tracks(group: str | None = None, tracks: list[int] | None = None) -> dict:
        """Hear only a group of mixer tracks: every other insert track is muted.

        Kept playing are the group's tracks, the tracks routed into them (a bus's
        sources) and the buses they go through, so the group sounds as in the mix.
        Undo with fl_clear_mute_solo.

        Args:
            group: Words matched in track names and routed channels' names.
            tracks: Insert track indexes, alone or added to the group's.

        Returns {"kept": [...], "muted": [...]} or {"error"}.
        """
        try:
            found = resolve_tracks(group, tracks)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        return call("mixer.muteAllExcept", {"tracks": found})

    @mcp.tool()
    def fl_clear_mute_solo() -> dict:
        """Unmute and unsolo every mixer track: the undo for fl_solo_tracks and fl_mute_tracks.

        Returns {"unmuted": [...], "unsoloed": [...]}.
        """
        return call("mixer.clearMuteSolo")

    @mcp.tool()
    def fl_set_colors(color: str, group: str | None = None, tracks: list[int] | None = None,
                      channels: list[int] | None = None) -> dict:
        """Colour mixer tracks and/or channels by colour name or hex code.

        Args:
            color: A name ("red", "light blue", ...) or a hex code ("#ff8800").
            group: Words matched in names: colours the matching channels and the
                mixer tracks they (or the words) match.
            tracks: Mixer track indexes to colour.
            channels: Channel indexes to colour.

        Returns {"color": "#rrggbb", "tracks": [...], "channels": [...]} or {"error"}.
        """
        try:
            red, green, blue = rgb(parse_color(color))
            if group is None and not tracks and not channels:
                raise ValueError("Give a group, tracks or channels to colour")
            track_list = sorted(set(tracks or []) | set(_group_or_none(resolve_tracks, group)))
            channel_list = sorted(
                set(channels or []) | set(_group_or_none(resolve_channels, group))
            )
            if group is not None and not track_list and not channel_list:
                raise ValueError(f"No mixer track or channel matches {group!r}")
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        for track in track_list:
            result = call("mixer.setTrackColor", {"track": track, "r": red, "g": green, "b": blue})
            if "error" in result:
                return {"error": f"Track {track}: {result['error']}"}
        for index in channel_list:
            result = call("channels.setColor", {"index": index, "r": red, "g": green, "b": blue})
            if "error" in result:
                return {"error": f"Channel {index}: {result['error']}"}
        return {
            "color": f"#{red:02x}{green:02x}{blue:02x}",
            "tracks": track_list,
            "channels": channel_list,
        }
