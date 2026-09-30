"""Tools that add an instrument plugin to the channel rack.

FL's scripting API can't create channels, so the plugin is loaded from the
browser's plugin database (`tools/browser.py`), where each generator is a file.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.tools.browser import open_in_new_channel, run_load
from fl_studio_mcp.utils.plugin_database import find_generator, list_generators

if TYPE_CHECKING:
    from fastmcp import FastMCP

GENERATORS_FOLDER = "Plugin database/Generators"


def register_channel_setup_tools(mcp: FastMCP) -> None:
    """Register channel creation tools with the MCP server."""

    @mcp.tool()
    def fl_list_generators() -> dict:
        """List the instrument plugins (generators) fl_add_channel can load.

        Read from FL Studio's plugin database, the same list the plugin picker
        (F8) shows. Returns {"generators": [{"name", "category"}, ...]}.
        """
        try:
            generators = list_generators()
        except ValueError as e:
            return {"error": str(e)}
        return {"generators": [
            {"name": name, "category": category} for name, category in generators.items()
        ]}

    @mcp.tool()
    def fl_add_channel(plugin: str, name: str | None = None) -> dict:
        """Add a channel with an instrument plugin to the end of the channel rack.

        The plugin starts with its default sound: use fl_load_in_new_channel
        instead to start from a sample or a preset. The new channel isn't routed
        to a mixer track (see fl_route_channel_to_mixer). Windows only. FL Studio
        needn't be in front.

        Args:
            plugin: Generator name as listed by fl_list_generators (case ignored),
                e.g. "3x Osc", "FLEX", "Sytrus".
            name: Name for the new channel. Default: the plugin's name.

        Returns {"index", "name", "plugin"} of the new channel, or {"error": ...}.
        A "warning" tells about a message box FL showed (a plugin that only
        runs as a demo in this edition).
        """
        try:
            generator = find_generator(plugin)
            category = list_generators()[generator]
        except ValueError as e:
            return {"error": str(e)}
        added = run_load(open_in_new_channel, f"{GENERATORS_FOLDER}/{category}/{generator}", name)
        if "error" in added:
            return added
        result = {"index": added["index"], "name": added["name"], "plugin": generator}
        if "warning" in added:
            result["warning"] = added["warning"]
        return result
