"""Pattern tools: list, select, rename, clone and find empty patterns (1-indexed); markers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_pattern_tools(mcp: FastMCP) -> None:
    """Register pattern tools with the MCP server."""

    @mcp.tool()
    def fl_get_patterns() -> dict:
        """List the patterns in use (index, name, length_beats) and the active one."""
        return call("patterns.getAll")

    @mcp.tool()
    def fl_select_pattern(index: int) -> dict:
        """Make a pattern active; the piano roll and step sequencer then edit it.

        Selecting an index that isn't used yet creates that pattern, so to start
        a new part use fl_find_empty_pattern and select the index it returns.

        Args:
            index: Pattern index, starting at 1.
        """
        if index < 1:
            return {"error": f"Pattern indexes start at 1, got {index}"}
        return call("patterns.select", {"index": index})

    @mcp.tool()
    def fl_rename_pattern(name: str, index: int | None = None) -> dict:
        """Rename a pattern. An empty name resets it to FL's default name.

        Args:
            name: New name.
            index: Pattern index (starting at 1); default is the active pattern.
        """
        params = {"name": name} if index is None else {"index": index, "name": name}
        return call("patterns.rename", params)

    @mcp.tool()
    def fl_find_empty_pattern() -> dict:
        """Index of the first unused pattern. It is not selected; use fl_select_pattern."""
        return call("patterns.findEmpty")

    @mcp.tool()
    def fl_clone_pattern(index: int, name: str | None = None) -> dict:
        """Copy a pattern, notes and steps of every channel, e.g. to write a variation of it.

        The copy becomes the active pattern (FL closes the piano roll while cloning).

        Args:
            index: The pattern to copy (starting at 1).
            name: Name for the copy; default is FL's.

        Returns {"index" (the copy), "source", "name"} or {"error"}.
        """
        result = call("patterns.clone", {"index": index})
        if "error" in result:
            return result
        # Seen live: FL leaves another pattern active (sometimes the copy, sometimes not).
        selected = call("patterns.select", {"index": result["index"]})
        if "error" in selected:
            return {**result, "warning": f"Cloned, but not selected: {selected['error']}"}
        if name is None:
            return result
        renamed = call("patterns.rename", {"index": result["index"], "name": name})
        if "error" in renamed:
            return {**result, "warning": f"Cloned, but not renamed: {renamed['error']}"}
        return {**result, "name": renamed.get("name", name)}

    @mcp.tool()
    def fl_add_marker(bar: float, name: str) -> dict:
        """Put a named time marker on the playlist's time line, e.g. "Drop" at bar 33.

        Markers label a song's sections for the user; they don't change the sound.

        Args:
            bar: Where it goes; 1 is the song's start, 33.5 half-way through bar 33.
            name: The marker's label.
        """
        return call("arrangement.addMarker", {"bar": bar, "name": name})

    @mcp.tool()
    def fl_get_markers() -> dict:
        """The time markers' names in time order. Their bars can't be read through the API.

        Returns {"markers": [{"index", "name"}, ...]}.
        """
        return call("arrangement.getMarkers")
