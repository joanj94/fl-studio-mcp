"""Tools that add channels to the channel rack.

FL's scripting API can't create channels, so these drive FL's plugin picker with
keystrokes (`utils/fl_keys.py`) and confirm the result through the controller.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_keys import add_generator_keystrokes
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.plugin_database import find_generator, list_generators

if TYPE_CHECKING:
    from fastmcp import FastMCP

# How long a plugin may take to load before the new channel shows up.
NEW_CHANNEL_TIMEOUT = 10.0
POLL_INTERVAL = 0.2


def _read_rack() -> list[dict] | None:
    """The rack's channels in order, or None if FL didn't answer."""
    try:
        channels = call("channels.getAll").get("channels")
    except RuntimeError:
        return None
    return channels if isinstance(channels, list) else None


def _names(rack: list[dict]) -> list[str]:
    return [channel.get("name", "") for channel in rack]


def _wait_for_rack_change(before: list[dict]) -> list[dict] | None:
    """Poll until the rack has a different number of channels; None on timeout."""
    deadline = time.monotonic() + NEW_CHANNEL_TIMEOUT
    while True:
        rack = _read_rack()
        if rack is not None and len(rack) != len(before):
            return rack
        if time.monotonic() >= deadline:
            return None
        time.sleep(POLL_INTERVAL)


def _new_channel_problem(before: list[dict], after: list[dict], generator: str) -> str | None:
    """Why `after` isn't `before` plus one `generator` channel at the end, or None."""
    index = len(before)
    if len(after) != index + 1 or _names(after)[:index] != _names(before):
        return (
            f"The channel rack changed in an unexpected way while adding {generator!r} "
            f"(it had {len(before)} channels, now {len(after)}). Check it with fl_get_all_channels."
        )
    added = after[index]
    identities = {str(added.get("name", "")).lower(), str(added.get("plugin") or "").lower()}
    if generator.lower() not in identities:
        return (
            f"Asked FL Studio for {generator!r} but the new channel {index} is "
            f"{added.get('name')!r}. It was left in place and not renamed."
        )
    return None


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

        Works by keystrokes (plugin picker: F8, plugin name, Enter), so FL Studio
        is brought to the front and must not be showing a dialog. It only picks
        the plugin: a "Sampler" channel is empty until a sample is loaded by hand.
        The new channel isn't routed to a mixer track (see fl_route_channel_to_mixer).

        Args:
            plugin: Generator name as listed by fl_list_generators (case ignored),
                e.g. "Sampler", "3x Osc", "FLEX".
            name: Name for the new channel. Default: the plugin's name.

        Returns {"index", "name", "plugin"} of the new channel, or {"error": ...}.
        """
        try:
            generator = find_generator(plugin)
        except ValueError as e:
            return {"error": str(e)}

        with keyboard_lock:
            before = _read_rack()
            if before is None:
                return {"error": "Could not read the channel rack from FL Studio"}
            if not add_generator_keystrokes(generator):
                return {"error": (
                    "Could not send the keystrokes to FL Studio: its window must be open "
                    "and able to take focus. If its plugin picker was left open, press Esc."
                )}
            after = _wait_for_rack_change(before)

        if after is None:
            return {"error": (
                f"Sent the keystrokes for {generator!r} but no channel was added. "
                "If FL Studio's plugin picker is still open, close it with Esc, "
                "and check the selected mixer track for an effect loaded by mistake."
            )}
        problem = _new_channel_problem(before, after, generator)
        if problem:
            return {"error": problem}

        index = len(before)
        result = {"index": index, "name": after[index].get("name", generator), "plugin": generator}
        closed = call("channels.showEditor", {"index": index, "show": False})
        if "error" in closed:
            result["warning"] = f"The plugin window could not be closed: {closed['error']}"
        if name:
            renamed = call("channels.setName", {"index": index, "name": name})
            if "error" in renamed:
                return {"error": f"Channel {index} was added but not renamed: {renamed['error']}"}
            result["name"] = name
        return result
