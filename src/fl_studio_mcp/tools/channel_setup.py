"""Tools that add an instrument plugin to the channel rack and delete channels.

FL's scripting API can't create channels, so the plugin is loaded from the
browser's plugin database (`tools/browser.py`), where each generator is a file.
It can't delete them either: that goes through the channel rack's menu
(`utils/fl_channel_rack.py`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.browser import open_in_new_channel, run_load
from fl_studio_mcp.utils import fl_channel_rack
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_channel_rack import ChannelRackError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.plugin_database import find_generator, list_generators
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

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

    @mcp.tool()
    def fl_delete_channels(channels: list[int | str]) -> dict:
        """Delete channels from the channel rack, with their notes in every pattern.

        The channels after a deleted one move up: read fl_get_all_channels again
        before using an index. FL keeps at least one channel. One undo step
        (fl_undo) brings them back. The channel rack must be showing; FL Studio
        needn't be in front. Windows only.

        Args:
            channels: Channel indexes (global, 0-based) or roles to delete.

        Returns {"deleted": [names], "remaining": channel count} or {"error": ...}.
        """
        try:
            indexes = sorted({resolve_channel(channel) for channel in channels})
            if not indexes:
                return {"error": "Give at least one channel to delete"}
            with keyboard_lock:
                rack = call("channels.getAll")
                if "error" in rack:
                    return rack
                names = {c["index"]: c["name"] for c in rack["channels"]}
                unknown = [index for index in indexes if index not in names]
                if unknown:
                    return {"error": f"No channel at index {unknown[0]}"}
                if len(indexes) == len(names):
                    return {"error": "FL Studio keeps at least one channel: leave one out"}
                remaining = fl_channel_rack.delete_channels(indexes)
        except (ValueError, ChannelRackError) as e:
            return {"error": str(e)}
        except Exception as e:
            logger.exception("Deleting channels failed")
            return {"error": f"Could not delete the channels: {e}"}
        return {"deleted": [names[index] for index in indexes], "remaining": remaining}
