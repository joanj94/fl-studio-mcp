"""Role tools: channels addressed by a word in their name (see utils/roles.py)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.roles import check_roles, get_channels

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_roles_tools(mcp: FastMCP) -> None:
    """Register role tools with the MCP server."""

    @mcp.tool()
    def fl_get_roles() -> dict:
        """List the channels an AI can address by role, with mixer track and plugin.

        Tools whose `channel` parameter accepts "index or role" take a word from a
        channel's name instead of its index: "kick" finds "808 Kick", "sub bass"
        finds "Sub Bass". An exact name wins; a role matching several channels is
        an error. To prepare a project, name each channel after its role.
        """
        try:
            channels = get_channels()
        except ValueError as e:
            return {"error": str(e)}
        return {"roles": [
            {
                "channel": c["index"],
                "name": c.get("name"),
                "mixer_track": c.get("target_fx_track"),
                "plugin": c.get("plugin"),
            }
            for c in channels
        ]}

    @mcp.tool()
    def fl_check_roles(roles: list[str]) -> dict:
        """Check that the open project has exactly one channel for each role.

        Use before writing a piece that needs specific parts, e.g.
        ["kick", "bass", "lead"]. Returns ok, found ({role: {channel, name}}),
        missing (no channel matches) and ambiguous ({role: [names]}).
        """
        try:
            return check_roles(roles, get_channels())
        except ValueError as e:
            return {"error": str(e)}
