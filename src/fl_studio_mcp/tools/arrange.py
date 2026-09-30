"""Arranging patterns into a song.

FL's scripting API can't place pattern clips in the playlist, so the
arrangement is written as notes into one long pattern instead: each section's
source patterns are looped for the section's length, channel by channel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.music.arrange import Section, layout_sections, tile_notes
from fl_studio_mcp.tools.piano_roll import read_notes, write_notes
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP


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
        target_pattern: int,
        channels: list[int | str] | None = None,
        name: str | None = None,
    ) -> dict:
        """Build a song from patterns by writing it into one long pattern.

        FL Studio's API can't place clips in the playlist, so this copies notes
        instead: sections play back to back, and in each section the listed
        patterns are looped to fill it. The result replaces `target_pattern`
        (play it in pattern mode); the source patterns are left untouched, so
        edit them and run this again to update the song. Step sequencer hits
        are copied as piano roll notes. Reads and writes every channel, so allow
        about half a second per channel and source pattern.

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
            "sections": [
                {
                    "name": section.name,
                    "start_bar": int(section.start // beats_per_bar) + 1,
                    "bars": int(section.length // beats_per_bar),
                    "patterns": list(section.patterns),
                }
                for section in laid_out
            ],
            "notes_written": notes_written,
        }
