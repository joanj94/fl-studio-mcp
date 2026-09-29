"""Project-level tools: tempo and an overview of the whole project."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Tempo range accepted by FL Studio (mirrors the controller's check).
MIN_TEMPO = 10.0
MAX_TEMPO = 522.0


def register_project_tools(mcp: FastMCP) -> None:
    """Register project tools with the MCP server."""

    @mcp.tool()
    def fl_get_project_overview() -> dict:
        """Summary of the open project, a good first call before composing.

        Returns tempo (bpm), timebase (ppq), beats_per_bar, the active pattern,
        used patterns (index, name, length_beats), channels (index, name,
        target mixer track), the mixer track count and the loop mode.
        Pattern indexes start at 1; channel indexes at 0.
        """
        return call("project.getInfo")

    @mcp.tool()
    def fl_get_tempo() -> dict:
        """Get the project tempo in BPM."""
        return call("project.getTempo")

    @mcp.tool()
    def fl_set_tempo(bpm: float) -> dict:
        """Set the project tempo.

        Args:
            bpm: Beats per minute, 10-522.
        """
        if not MIN_TEMPO <= bpm <= MAX_TEMPO:
            return {"error": f"Tempo must be {MIN_TEMPO}-{MAX_TEMPO} BPM, got {bpm}"}
        return call("project.setTempo", {"bpm": bpm})
