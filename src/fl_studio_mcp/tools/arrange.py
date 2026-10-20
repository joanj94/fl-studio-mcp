"""Arranging patterns into a song.

`fl_arrange` puts pattern clips on the playlist's tracks, one track per
pattern, the way a song is laid out by hand (`utils/fl_playlist.py`; Windows).
`fl_arrange_in_pattern` is the older way, for where that can't be used: the
arrangement is written as notes into one long pattern.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.music.arrange import Section, clip_bars, layout_sections, tile_notes
from fl_studio_mcp.music.midi_file import write_midi
from fl_studio_mcp.tools.piano_roll import read_notes, write_notes
from fl_studio_mcp.utils import fl_playlist
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_playlist import PlaylistError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

MAX_PLAYLIST_TRACKS = 500
SNAP_WARNING = "The playlist's snap could not be set back to Main (it is on Bar); set it by hand."
# How far down the playlist left-over track names are looked for after a replace.
NAMED_TRACKS_CHECKED = 60


def _describe(sections: list[Section], beats_per_bar: int) -> list[dict]:
    return [
        {
            "name": section.name,
            "start_bar": int(section.start // beats_per_bar) + 1,
            "bars": int(section.length // beats_per_bar),
            "patterns": list(section.patterns),
        }
        for section in sections
    ]


def place_in_playlist(
    bars: dict[int, list[int]], tracks: dict[int, int], lengths_bars: dict[int, float],
    names: dict[int, str], replace: bool, pattern_names: set[str],
) -> dict:
    """Empty the playlist and put each pattern's clips on its track; raises PlaylistError.

    The clip that ends the song goes first: in the empty playlist its place is
    checked by the length the song gets, and every other clip lies inside that
    length, where each click can be aimed (see `fl_playlist`).
    """
    panel = fl_playlist.open_panel()
    before = fl_playlist.state()
    floor = 0.0  # how long the time markers alone make the song
    if before["length_bars"]:
        fl_playlist._fl("transport.setLoopMode", {"mode": "song"})  # markers are found in it
        floor = fl_playlist.marker_extent()
    if before["length_bars"] and not replace:
        if before["length_bars"] <= floor + fl_playlist.LENGTH_TOLERANCE_BARS:
            raise PlaylistError(
                f"The time markers make the song {floor:g} bars long, so it can't be told "
                "whether the playlist holds clips. Pass replace=true to delete any clips "
                "in it and lay the song out anew (the markers stay)."
            )
        raise PlaylistError(
            f"The playlist already holds a song of {before['length_bars']:g} bars. Pass "
            "replace=true to delete every clip in it and lay the song out anew."
        )
    # FL keeps a mute on the track number, so a pattern moved to another track
    # would leave its mute behind (and pick up the new track's): read who was muted.
    listed = fl_playlist._fl("playlist.getTracks", {"count": NAMED_TRACKS_CHECKED})["tracks"]
    muted_names = {e["name"] for e in listed if e["is_muted"] and e["name"] in pattern_names}
    fl_playlist._fl("transport.setLoopMode", {"mode": "song"})
    fl_playlist.clear(floor)
    fl_playlist.zoom_for_placing()
    ends = {pattern: max(starts) - 1 + lengths_bars[pattern] for pattern, starts in bars.items()}
    last = max(ends, key=lambda pattern: ends[pattern])
    # A song that ends inside the markers' length gets no longer: every clip is aimed.
    ends_song = ends[last] > floor + fl_playlist.LENGTH_TOLERANCE_BARS
    snap_left = False
    fl_playlist.snap_to_bars(True)
    try:
        for pattern in [last, *[p for p in bars if p != last]]:
            fl_playlist._fl("patterns.select", {"index": pattern})
            y = fl_playlist.find_track(panel, tracks[pattern])
            starts = list(bars[pattern])
            if pattern == last and ends_song:
                starts.remove(max(starts))
                fl_playlist.place_last_clip(panel, y, max(bars[pattern]), ends[pattern])
            fl_playlist.place_clips(panel, y, starts, before["bar_ticks"])
    finally:
        try:
            fl_playlist.snap_to_bars(False)
        except PlaylistError:
            fl_playlist.close_menus_left_open()
            snap_left = True
        try:  # aiming moved the song position along the ruler
            call("transport.setPosition", {"position": 0, "mode": 2})
        except RuntimeError:
            pass  # FL isn't answering: that is reported by whatever failed above
    for pattern, track in tracks.items():
        fl_playlist._fl("playlist.setTrackName", {"track": track, "name": names[pattern]})
        fl_playlist._fl(
            "playlist.setTrackMuted", {"track": track, "muted": names[pattern] in muted_names}
        )
    # A track an earlier, longer arrangement named after its pattern is empty now;
    # an empty name resets it. Names that aren't a pattern's are someone else's.
    listed = fl_playlist._fl("playlist.getTracks", {"count": NAMED_TRACKS_CHECKED})["tracks"]
    for entry in listed:
        if entry["track"] not in tracks.values() and entry["name"] in pattern_names:
            fl_playlist._fl("playlist.setTrackName", {"track": entry["track"], "name": ""})
    fl_playlist._fl("playlist.scrollTo", {"bar": 1, "track": min(tracks.values())})
    state = fl_playlist.state()
    return {**state, "warning": SNAP_WARNING} if snap_left else state


def _arranged_notes(
    sections: list[Section], notes_by_pattern: dict[int, list[dict]], lengths: dict[int, float]
) -> list[dict]:
    """One channel's notes for the whole arrangement."""
    arranged: list[dict] = []
    for section in sections:
        for pattern in section.patterns:
            arranged += tile_notes(
                notes_by_pattern[pattern], lengths[pattern], section.start, section.length
            )
    return arranged


def register_arrange_tools(mcp: FastMCP) -> None:
    """Register arrangement tools with the MCP server."""

    @mcp.tool()
    def fl_arrange(
        sections: list[dict],
        replace: bool = False,
        first_track: int = 1,
    ) -> dict:
        """Build the song in the playlist: pattern clips on playlist tracks, section by section.

        This is how a song is made: write each part as a pattern of its own
        (one pattern for the kick, one for the lead riff, ...; a pattern may use
        several channels), then lay them out here. Sections play back to back;
        in each one the listed patterns play together, each repeated to fill
        the section. Every pattern gets its own playlist track, named after it.
        FL is switched to song mode, so fl_play and fl_render give the song.
        Edit a pattern and the song follows; run this again (replace=true) to
        change the layout. Takes about a third of a second per clip; FL stays
        in the background. Windows only (elsewhere: fl_arrange_in_pattern).

        Args:
            sections: In order, e.g. [{"name": "intro", "bars": 8, "patterns": [1]},
                {"name": "break", "bars": 4, "patterns": []},
                {"name": "climax", "bars": 16, "patterns": [1, 3, 4]}].
                "patterns" are the pattern indexes playing together in that
                section (empty = silence); "name" is optional. A section must
                be a whole number of repeats of each of its patterns (a 4-bar
                pattern fits 4, 8, 12... bars). For a pattern that should not
                repeat back to back, give {"pattern": 9, "every_bars": 8}
                instead of its index: a one-bar crash then plays at the start
                of every 8 bars ("every_bars" as long as the section: once).
                Add "from_bar": 5 to start the first one on the section's
                5th bar instead of its first (a 4-bar build at the end of an
                8-bar section).
            replace: Delete every clip already in the playlist first. Needed
                whenever the playlist isn't empty.
            first_track: Playlist track (1-based) of the first pattern; the
                others follow below it, in the order they first appear.

        Returns {"total_bars", "sections", "tracks": [{"track", "pattern", "name",
        "clips"}], "mode": "song"} or {"error": ...}.
        """
        info = call("project.getInfo")
        if "error" in info:
            return info
        beats_per_bar = info.get("beats_per_bar") or 4
        patterns = {p["index"]: p for p in info.get("patterns", [])}
        lengths = {index: p["length_beats"] for index, p in patterns.items()}
        try:
            laid_out = layout_sections(sections, beats_per_bar)
            bars = clip_bars(laid_out, lengths, beats_per_bar)
        except ValueError as e:
            return {"error": str(e)}
        if not bars:
            return {"error": "No section has a pattern: there is nothing to place"}
        last_track = first_track + len(bars) - 1 if isinstance(first_track, int) else 0
        if isinstance(first_track, bool) or not 1 <= first_track <= last_track <= (
            MAX_PLAYLIST_TRACKS
        ):
            return {"error": f"first_track must be a playlist track (1 or more) that leaves room "
                             f"for {len(bars)} tracks, got {first_track!r}"}
        tracks = {pattern: first_track + offset for offset, pattern in enumerate(bars)}
        names = {pattern: patterns[pattern].get("name") or f"Pattern {pattern}" for pattern in bars}
        lengths_bars = {pattern: lengths[pattern] / beats_per_bar for pattern in bars}
        try:
            with keyboard_lock:
                state = place_in_playlist(
                    bars, tracks, lengths_bars, names, replace,
                    {p["name"] for p in patterns.values() if p.get("name")},
                )
        except PlaylistError as e:
            fl_playlist.close_menus_left_open()
            return {"error": str(e)}
        except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
            fl_playlist.close_menus_left_open()
            return {"error": f"The playlist could not be driven: {e!r}"}
        result = {
            "total_bars": int(laid_out[-1].end // beats_per_bar),
            "song_length_bars": state["length_bars"],
            "sections": _describe(laid_out, beats_per_bar),
            "tracks": [
                {"track": tracks[pattern], "pattern": pattern, "name": names[pattern],
                 "clips": len(bars[pattern])}
                for pattern in bars
            ],
            "mode": "song",
        }
        if "warning" in state:
            result["warning"] = state["warning"]
        return result

    def _edit_clips(edit) -> dict:
        """Run `edit(panel)` with the playlist ready for single clips.

        Returns {"song_length_bars", "warning"?} or {"error"}.
        """
        snap_left = False
        try:
            with keyboard_lock:
                panel = fl_playlist.open_panel()
                fl_playlist._fl("transport.setLoopMode", {"mode": "song"})
                fl_playlist.zoom_for_placing()
                fl_playlist.snap_to_bars(True)
                try:
                    edit(panel)
                finally:
                    try:
                        fl_playlist.snap_to_bars(False)
                    except PlaylistError:
                        fl_playlist.close_menus_left_open()
                        snap_left = True
                    try:  # aiming moved the song position along the ruler
                        call("transport.setPosition", {"position": 0, "mode": 2})
                    except RuntimeError:
                        pass
            result = {"song_length_bars": fl_playlist.state()["length_bars"]}
        except PlaylistError as e:
            fl_playlist.close_menus_left_open()
            return {"error": str(e)}
        except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
            fl_playlist.close_menus_left_open()
            return {"error": f"The playlist could not be driven: {e!r}"}
        return {**result, "warning": SNAP_WARNING} if snap_left else result

    @mcp.tool()
    def fl_add_clip(pattern: int, track: int, bar: int) -> dict:
        """Put one clip of a pattern in the playlist, without touching the other clips.

        For changing a song fl_arrange laid out: add a fill, a crash, one more
        bar of a part. The cell must be empty. Checked by a picture of the
        playlist (or, past the song's end, by the song's new length). Windows only.

        Args:
            pattern: Pattern index (1-based).
            track: Playlist track (1-based).
            bar: The bar the clip starts on (1 = the song's start).

        Returns {"pattern", "track", "bar", "song_length_bars"} or {"error"}.
        """
        info = call("project.getInfo")
        if "error" in info:
            return info
        lengths = {p["index"]: p["length_beats"] for p in info.get("patterns", [])}
        if not lengths.get(pattern):
            return {"error": f"Pattern {pattern} is empty or doesn't exist"}
        for name, value in (("track", track), ("bar", bar)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return {"error": f"{name} must be 1 or more, got {value!r}"}
        length_bars = lengths[pattern] / (info.get("beats_per_bar") or 4)

        def edit(panel) -> None:
            fl_playlist._fl("patterns.select", {"index": pattern})
            fl_playlist.add_clip(panel, track, bar, length_bars)

        outcome = _edit_clips(edit)
        if "error" in outcome:
            return outcome
        return {"pattern": pattern, "track": track, "bar": bar, **outcome}

    @mcp.tool()
    def fl_delete_clip(track: int, bar: int) -> dict:
        """Delete the one playlist clip that covers a bar on a track. Windows only.

        Checked by a picture of the playlist. The bar must lie inside the song.

        Args:
            track: Playlist track (1-based).
            bar: A bar the clip covers (1 = the song's start).

        Returns {"track", "bar", "song_length_bars"} or {"error"}.
        """
        for name, value in (("track", track), ("bar", bar)):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                return {"error": f"{name} must be 1 or more, got {value!r}"}
        outcome = _edit_clips(lambda panel: fl_playlist.delete_clip(panel, track, bar))
        if "error" in outcome:
            return outcome
        return {"track": track, "bar": bar, **outcome}

    @mcp.tool()
    def fl_get_playlist(tracks: int = 20) -> dict:
        """The playlist's state: song length and the first tracks' names.

        FL's API can't list the clips themselves; fl_screenshot shows them.

        Returns {"visible", "length_bars" (0: no clips), "tracks": [{"track",
        "name", "is_muted"}]} or {"error": ...}.
        """
        state = call("playlist.getState")
        if "error" in state:
            return state
        listed = call("playlist.getTracks", {"count": tracks})
        if "error" in listed:
            return listed
        return {"visible": state["visible"], "length_bars": state["length_bars"],
                "tracks": listed["tracks"]}

    @mcp.tool()
    def fl_arrange_in_pattern(
        sections: list[dict],
        target_pattern: int,
        channels: list[int | str] | None = None,
        name: str | None = None,
    ) -> dict:
        """Build a song from patterns by writing it as notes into one long pattern.

        Prefer fl_arrange, which lays the song out in the playlist as clips;
        use this only where that doesn't work (not on Windows). Sections play
        back to back, and in each section the listed patterns are looped to
        fill it. The result replaces `target_pattern` (play it in pattern
        mode); the source patterns are left untouched, so edit them and run
        this again to update the song. Step sequencer hits are copied as piano
        roll notes. Reads and writes every channel, so allow about half a
        second per channel and source pattern.

        Args:
            sections: In order, e.g. [{"name": "intro", "bars": 8, "patterns": [1]},
                {"name": "break", "bars": 4, "patterns": []},
                {"name": "climax", "bars": 16, "patterns": [1, 3, 4]}].
                "patterns" are the pattern indexes playing together in that
                section (empty = silence); "name" is optional.
            target_pattern: Pattern index (1-based) to write the song into. It
                becomes the active pattern. Must not be one of the sources.
            channels: Channels (index or role) to include; default: all. Other
                channels keep whatever they already have in the target pattern.
            name: New name for the target pattern.

        Returns the section start bars and the notes written per channel.
        """
        info = call("project.getInfo")
        if "error" in info:
            return info
        beats_per_bar = info.get("beats_per_bar") or 4
        lengths = {p["index"]: p["length_beats"] for p in info.get("patterns", [])}

        try:
            laid_out = layout_sections(sections, beats_per_bar)
        except ValueError as e:
            return {"error": str(e)}
        sources = sorted({pattern for section in laid_out for pattern in section.patterns})
        if isinstance(target_pattern, bool) or not isinstance(target_pattern, int) or (
            target_pattern < 1
        ):
            return {"error": f"target_pattern must be a pattern index (1 or more), "
                             f"got {target_pattern!r}"}
        if target_pattern in sources:
            return {"error": f"Pattern {target_pattern} is a source; pick another target_pattern"}
        unused = [pattern for pattern in sources if not lengths.get(pattern)]
        if unused:
            return {"error": f"Pattern(s) {unused} are empty, so they can't be arranged"}

        names = {channel["index"]: channel["name"] for channel in info.get("channels", [])}
        try:
            wanted = list(names) if channels is None else [resolve_channel(c) for c in channels]
        except ValueError as e:
            return {"error": str(e)}
        if not wanted:
            return {"error": "No channels to arrange"}

        notes_written: dict[str, int] = {}
        try:
            for channel in wanted:
                by_pattern = {pattern: read_notes(channel, pattern) for pattern in sources}
                arranged = _arranged_notes(laid_out, by_pattern, lengths)
                # An unused target has nothing to clear, so silent channels are skipped.
                if arranged or target_pattern in lengths:
                    write_notes(arranged, channel, target_pattern)
                if arranged:
                    label = names.get(channel, str(channel))
                    if label in notes_written:  # two channels with the same name
                        label = f"{label} (channel {channel})"
                    notes_written[label] = len(arranged)
        except (ValueError, TypeError) as e:
            return {"error": (
                f"Stopped while arranging channel {channel}: {e} "
                f"Channels done so far: {sorted(notes_written)}."
            )}

        # The last channel read may have left a source pattern active.
        selected = call("patterns.select", {"index": target_pattern})
        if "error" in selected:
            return selected
        if name:
            renamed = call("patterns.rename", {"index": target_pattern, "name": name})
            if "error" in renamed:
                return renamed
        return {
            "pattern": target_pattern,
            "total_bars": int(laid_out[-1].end // beats_per_bar),
            "sections": _describe(laid_out, beats_per_bar),
            "notes_written": notes_written,
        }

    @mcp.tool()
    def fl_export_midi(
        path: str,
        pattern: int | None = None,
        sections: list[dict] | None = None,
        channels: list[int | str] | None = None,
    ) -> dict:
        """Export notes from FL Studio as a standard MIDI file, one track per channel.

        Either one pattern (default: the active one), or a whole song given as
        sections like fl_arrange takes them (the playlist itself can't be read).
        Step sequencer hits are exported as short notes. Reads every channel's
        piano roll, so allow about half a second per channel and pattern.

        Args:
            path: Where to write it; must end in .mid.
            pattern: The pattern to export (1-based).
            sections: The song, e.g. [{"bars": 8, "patterns": [1]}, {"bars": 16,
                "patterns": [1, 2, 3]}]; instead of `pattern`.
            channels: Channels (index or role) to include; default: every channel
                that isn't an automation clip. Channels without notes are left out.

        Returns {"path", "tracks", "notes", "channels"} or {"error"}.
        """
        info = call("project.getInfo")
        if "error" in info:
            return info
        beats_per_bar = info.get("beats_per_bar") or 4
        lengths = {p["index"]: p["length_beats"] for p in info.get("patterns", [])}
        rack = info.get("channels", [])
        names = {c["index"]: c["name"] for c in rack}
        current = info.get("current_pattern")
        tracks: list[dict] = []
        try:
            wanted = ([c["index"] for c in rack if not c.get("is_automation")]
                      if channels is None else [resolve_channel(c) for c in channels])
            if sections is not None:
                laid_out = layout_sections(sections, beats_per_bar)
                sources = sorted({p for section in laid_out for p in section.patterns})
            else:
                sources = [current if pattern is None else pattern]
            unused = [p for p in sources if not lengths.get(p)]
            if unused:
                raise ValueError(f"Pattern(s) {unused} are empty")
            for channel in wanted:
                by_pattern = {p: read_notes(channel, p) for p in sources}
                notes = (_arranged_notes(laid_out, by_pattern, lengths) if sections is not None
                         else by_pattern[sources[0]])
                if notes:
                    tracks.append({"name": names.get(channel, f"Channel {channel}"),
                                   "notes": notes})
            if not tracks:
                raise ValueError("None of those channels has notes there")
            result = write_midi(Path(path), tracks, info.get("bpm") or 120, beats_per_bar)
        except (OSError, ValueError, TypeError, OverflowError, RuntimeError) as e:
            return {"error": str(e)}
        finally:
            # Reading leaves another pattern active: put back the one that was.
            if current:
                try:
                    call("patterns.select", {"index": current})
                except RuntimeError:
                    pass  # FL isn't answering: whatever failed above says so
        return {**result, "channels": [t["name"] for t in tracks]}

    @mcp.tool()
    def fl_set_playlist_selection(start_bar: float | None = None,
                                  end_bar: float | None = None) -> dict:
        """Select bars in the playlist (a time selection), or clear the selection.

        In song mode FL then loops playback over the selection (handy to listen
        to one section with fl_play). To render some bars, fl_render takes
        start_bar/end_bar itself.

        Args:
            start_bar: First bar (1 is the song's start). Omit both to clear.
            end_bar: The bar the selection ends at (not included).

        Returns {"active", "start_bar", "end_bar"} or {"error"}.
        """
        if start_bar is None and end_bar is None:
            return call("arrangement.clearSelection")
        return call("arrangement.setSelection", {"start_bar": start_bar, "end_bar": end_bar})

    @mcp.tool()
    def fl_get_playlist_selection() -> dict:
        """The playlist's time selection, if any: {"active", "start_bar", "end_bar"}."""
        return call("arrangement.getSelection")
