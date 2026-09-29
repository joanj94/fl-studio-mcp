"""Pattern tools: list, select, rename and find empty patterns (1-indexed)."""

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
