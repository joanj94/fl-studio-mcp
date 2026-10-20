"""Plugin control tools for FL Studio.

NOTE: These tools can only control EXISTING plugins. FL Studio's scripting API
does not support loading new plugins programmatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.utils.param_values import find_value
from fl_studio_mcp.utils.roles import resolve_channel

PRESET_SETTLE_SECONDS = 1.0

if TYPE_CHECKING:
    from fastmcp import FastMCP


def _plugin_index(index: int | str, slot_index: int) -> int:
    """Channel index for a channel plugin (roles allowed), or the mixer track index.

    Mixer effects (slot_index 0 or more) are addressed by track number only.
    Raises ValueError if the value can't be resolved.
    """
    if slot_index < 0:
        return resolve_channel(index)
    if isinstance(index, int) and not isinstance(index, bool):
        return index
    if isinstance(index, str) and index.strip().isdigit():
        return int(index.strip())
    raise ValueError(f"A mixer track must be given by its number, got {index!r}")


def param_by_name(conn, plugin_index: int, slot_index: int, param: int | str) -> tuple[int, str]:
    """(index, name) of a parameter given by index or by name. Raises ValueError.

    A name matches exactly (any case), or else as words of exactly one parameter's name.
    """
    if isinstance(param, int) and not isinstance(param, bool):
        result = conn.send_command("plugins.getParamValue", {
            "param_index": param, "plugin_index": plugin_index, "slot_index": slot_index,
        })
        if not result.get("success", False):
            raise ValueError(result.get("error") or f"No parameter {param}")
        return param, result.get("name", "")
    result = conn.send_command("plugins.getParams", {
        "index": plugin_index, "slot_index": slot_index, "search": str(param),
    })
    if not result.get("success", False):
        raise ValueError(result.get("error") or "FL Studio didn't list the parameters")
    found = result.get("params", [])
    exact = [p for p in found if p["name"].lower().strip() == str(param).lower().strip()]
    matches = exact or found
    if len(matches) == 1:
        return matches[0]["index"], matches[0]["name"]
    if not matches:
        raise ValueError(f"No parameter is named {param!r}; fl_get_plugin_params lists them")
    names = ", ".join(f"{p['index']}: {p['name']}" for p in matches[:10])
    raise ValueError(f"{param!r} matches several parameters ({names}); give its index")


def set_param_by_value(conn, plugin_index: int, slot_index: int, index: int,
                       value: str) -> dict:
    """Set parameter `index` to show `value` (see utils/param_values.py).

    Returns {"param", "name", "value", "shows"}; on failure the parameter is put
    back as it was and ValueError raised.
    """
    where = {"param_index": index, "plugin_index": plugin_index, "slot_index": slot_index}

    def get() -> dict:
        result = conn.send_command("plugins.getParamValue", where)
        if not result.get("success", False):
            raise ValueError(result.get("error") or "FL Studio didn't answer")
        return result

    def read_at(position: float) -> str:
        result = conn.send_command("plugins.setParamValue", {**where, "value": position})
        if not result.get("success", False):
            raise ValueError(result.get("error") or "FL Studio didn't answer")
        return str(get().get("value_string", ""))

    original = get()
    try:
        position, shows = find_value(value, read_at)
    except ValueError as e:
        conn.send_command("plugins.setParamValue", {**where, "value": original["value"]})
        raise ValueError(f"{original.get('name') or index}: {e}") from e
    return {"param": index, "name": original.get("name", ""), "value": round(position, 6),
            "shows": shows}


def register_plugin_tools(mcp: FastMCP) -> None:
    """Register plugin control tools with the MCP server."""
    from fl_studio_mcp.utils.connection import get_connection

    @mcp.tool()
    def fl_is_plugin_valid(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> bool:
        """Check if a plugin exists at the given location.

        For channel rack plugins, use just index.
        For mixer effect plugins, use index as mixer track and slot_index as effect slot.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError:
            return False
        conn = get_connection()
        result = conn.send_command("plugins.isValid", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return False

        return result.get("valid", False)

    @mcp.tool()
    def fl_get_plugin_name(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> str:
        """Get the name of a plugin.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return f"Error: {e}"
        conn = get_connection()
        result = conn.send_command("plugins.getName", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return f"Error: {result['error']}"

        return result.get("name", "")

    @mcp.tool()
    def fl_get_plugin_param_count(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> int:
        """Get the number of parameters a plugin has.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError:
            return -1
        conn = get_connection()
        result = conn.send_command("plugins.getParamCount", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return -1

        return result.get("count", 0)

    @mcp.tool()
    def fl_get_plugin_params(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True,
        max_params: int = 50,
        search: str | None = None,
    ) -> list[dict]:
        """Get a plugin's parameters with their current values.

        A plugin can have thousands: without `search` only the first
        `max_params` come back. Search by name to find the one you need, e.g.
        "cutoff", "pitch envelope", "attack".

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
            max_params: Maximum number of parameters to return (default 50)
            search: Words that must all be in the parameter's name (any case)
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return [{"error": str(e)}]
        conn = get_connection()
        result = conn.send_command("plugins.getParams", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
            "max_params": max_params,
            "search": search,
        })

        if not result.get("success", False) and "error" in result:
            return [{"error": result["error"]}]

        return result.get("params", [])

    @mcp.tool()
    def fl_get_plugin_param_value(
        param_index: int,
        plugin_index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> dict:
        """Get the value of a specific plugin parameter.

        Args:
            param_index: Parameter index
            plugin_index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            plugin_index = _plugin_index(plugin_index, slot_index)
        except ValueError as e:
            return {"error": str(e)}
        conn = get_connection()
        result = conn.send_command("plugins.getParamValue", {
            "param_index": param_index,
            "plugin_index": plugin_index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return {"error": result["error"]}

        return {
            "index": result.get("index", param_index),
            "name": result.get("name", ""),
            "value": result.get("value", 0),
            "value_string": result.get("value_string", ""),
        }

    @mcp.tool()
    def fl_set_plugin_param_value(
        param_index: int,
        value: float,
        plugin_index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> str:
        """Set the value of a plugin parameter.

        Args:
            param_index: Parameter index
            value: New value (typically 0.0 to 1.0, but depends on parameter)
            plugin_index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            plugin_index = _plugin_index(plugin_index, slot_index)
        except ValueError as e:
            return f"Error: {e}"
        conn = get_connection()
        result = conn.send_command("plugins.setParamValue", {
            "param_index": param_index,
            "value": value,
            "plugin_index": plugin_index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return f"Error: {result['error']}"

        # Seen live: some plugins report the old value within the setting command.
        fresh = conn.send_command("plugins.getParamValue", {
            "param_index": param_index,
            "plugin_index": plugin_index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })
        if fresh.get("success", False) and "value" in fresh:
            result = fresh
        name = result.get("name", f"Parameter {param_index}")
        new_value = result.get("value", value)
        value_str = result.get("value_string", "")
        return f"Parameter '{name}' set to {new_value:.4f} ({value_str})"

    @mcp.tool()
    def fl_set_plugin_param_by_value(
        plugin_index: int | str,
        param: int | str,
        value: str,
        slot_index: int = -1,
    ) -> dict:
        """Set a plugin parameter to what it should show, in its own units.

        E.g. "250Hz", "4.5kHz", "-6dB", "1.5sec", "120ms", "35%", "4:1" (a ratio),
        or a named setting such as "Peaking" or "High pass". The 0-1 position is
        found by trying values and reading what the plugin shows, so it works
        for any plugin; numbers take about a second, named settings a few.

        Args:
            plugin_index: Channel index or role, or the mixer track for an effect.
            param: Parameter index, or its name ("Band 2 freq"; see fl_get_plugin_params).
            value: What the parameter should show; use the unit it shows.
            slot_index: Effect slot on the mixer track (-1 for a channel's plugin).

        Returns {"param", "name", "value" (0-1), "shows"} or {"error"}.
        """
        conn = get_connection()
        try:
            plugin_index = _plugin_index(plugin_index, slot_index)
            index, _ = param_by_name(conn, plugin_index, slot_index, param)
            return set_param_by_value(conn, plugin_index, slot_index, index, value)
        except ValueError as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_get_preset_count(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> int:
        """Get the number of presets available for a plugin.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError:
            return -1
        conn = get_connection()
        result = conn.send_command("plugins.getPresetCount", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return -1

        return result.get("count", 0)

    @mcp.tool()
    def fl_list_presets(index: int | str, slot_index: int = -1) -> dict:
        """List the names of a plugin's own presets, in the order fl_next_preset steps through.

        Only plugins with built-in presets have any (FLEX lists the presets of
        its current pack). For other sounds see fl_browser_list and
        fl_load_in_channel.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)

        Returns {"count", "presets": [names]} or {"error": ...}.
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return {"error": str(e)}
        result = get_connection().send_command("plugins.listPresets", {
            "index": index,
            "slot_index": slot_index,
        })
        if not result.get("success", False) and "error" in result:
            return {"error": result["error"]}
        return {"count": result.get("count", 0), "presets": result.get("presets", [])}

    @mcp.tool()
    def fl_select_preset(index: int | str, name: str, slot_index: int = -1) -> dict:
        """Switch a plugin to one of its own presets by name (fl_list_presets lists them).

        An exact name wins, else a part of a name that only one preset has
        ("behemoth"). Checked by reading the current preset back.

        Args:
            index: Channel index (global) or role, or mixer track index.
            name: The preset's name or a unique part of it (case ignored).
            slot_index: Effect slot for a mixer effect (-1 for a channel's plugin).

        Returns {"index", "preset", "steps"} or {"error": ...}.
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return {"error": str(e)}
        import time

        from fl_studio_mcp.utils.connection import call

        selected = call("plugins.selectPreset",
                        {"index": index, "name": name, "slot_index": slot_index})
        if "error" in selected:
            return selected
        # Seen live: the current preset only reads back after the stepping command.
        deadline = time.monotonic() + PRESET_SETTLE_SECONDS
        while True:
            now = call("plugins.getCurrentPreset", {"index": index, "slot_index": slot_index})
            if "error" in now or now.get("preset") == selected["preset"]:
                break
            if time.monotonic() >= deadline:
                return {"error": f"The plugin is on {now.get('preset')!r} after stepping to "
                                 f"{selected['preset']!r}"}
            time.sleep(0.05)
        return {**selected, **({"error": now["error"]} if "error" in now else {})}

    @mcp.tool()
    def fl_get_drum_pads(channel: int | str) -> dict:
        """List a drum plugin's pads (FPC): the MIDI note each pad plays, its colour, its name.

        Write notes at a pad's note to play that pad (fl_send_notes).

        Returns {"index", "pads": [{"pad", "note", "color", "name"?}]} or {"error"}.
        """
        try:
            index = resolve_channel(channel)
        except ValueError as e:
            return {"error": str(e)}
        from fl_studio_mcp.utils.connection import call

        return call("plugins.getPads", {"index": index})

    @mcp.tool()
    def fl_next_preset(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> str:
        """Switch to the next preset for a plugin.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return f"Error: {e}"
        conn = get_connection()
        result = conn.send_command("plugins.nextPreset", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return f"Error: {result['error']}"

        plugin_name = result.get("plugin_name", "Plugin")
        return f"Switched '{plugin_name}' to next preset"

    @mcp.tool()
    def fl_prev_preset(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> str:
        """Switch to the previous preset for a plugin.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return f"Error: {e}"
        conn = get_connection()
        result = conn.send_command("plugins.prevPreset", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return f"Error: {result['error']}"

        plugin_name = result.get("plugin_name", "Plugin")
        return f"Switched '{plugin_name}' to previous preset"

    @mcp.tool()
    def fl_get_plugin_color(
        index: int | str,
        slot_index: int = -1,
        use_global_index: bool = True
    ) -> str:
        """Get the color of a plugin.

        Args:
            index: Channel index (global) or role, or mixer track index
            slot_index: Effect slot index for mixer plugins (-1 for channel rack)
            use_global_index: Whether to use global channel indexing
        """
        try:
            index = _plugin_index(index, slot_index)
        except ValueError as e:
            return f"Error: {e}"
        conn = get_connection()
        result = conn.send_command("plugins.getColor", {
            "index": index,
            "slot_index": slot_index,
            "use_global": use_global_index,
        })

        if not result.get("success", False) and "error" in result:
            return f"Error: {result['error']}"

        return result.get("color", "0x0")
