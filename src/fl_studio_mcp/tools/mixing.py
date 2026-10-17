"""Mixing tools: musical moves (intents) and processing chains on mixer tracks.

The moves are data (mixing/intents.json) on FL's stock effects; each value is
set by what the plugin shows (utils/param_values.py). A plugin the track lacks
is added from the plugin database first, so a chain is built, not suggested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fl_studio_mcp.mixing import intents as mixing
from fl_studio_mcp.tools.browser import add_effect, run_load
from fl_studio_mcp.tools.plugins import param_by_name, set_param_by_value
from fl_studio_mcp.utils import connection
from fl_studio_mcp.utils.param_values import parse_display
from fl_studio_mcp.utils.plugin_database import effect_browser_path

if TYPE_CHECKING:
    from fastmcp import FastMCP

# EQ band types whose level is the only thing that changes the sound.
LEVEL_TYPES = ("peaking", "low shelf", "high shelf", "disabled")
FLAT_DB = 0.3  # a band within this many dB of 0 does nothing
SAME_FREQUENCY = 0.05  # a band within 5 % of an intent's frequency is that intent's band


def _effects(track: int) -> list[dict]:
    result = connection.call("mixer.getTrackEffects", {"track": track})
    if "error" in result:
        raise ValueError(result["error"])
    return result.get("effects", [])


def find_or_add_plugin(track: int, plugin: str, slot: int | None = None) -> tuple[int, bool]:
    """(slot, added) of `plugin` on the track: the given slot, the first one holding it,
    or a new one loaded from the plugin database. Raises ValueError."""
    effects = _effects(track)
    if slot is not None:
        found = [e for e in effects if e["slot"] == slot]
        if not found or found[0]["plugin"].lower() != plugin.lower():
            raise ValueError(f"Slot {slot} of track {track} doesn't hold {plugin}")
        return slot, False
    for effect in effects:
        if effect["plugin"].lower() == plugin.lower():
            return effect["slot"], False
    result = run_load(add_effect, effect_browser_path(plugin), track)
    if "error" in result:
        raise ValueError(f"{plugin} could not be added to track {track}: {result['error']}")
    return result["slot"], True


def _bands(conn, track: int, slot: int) -> dict[int, dict[str, str]]:
    """{band: {"type", "level", "freq"}} of a Parametric EQ 2, as displayed."""
    result = conn.send_command("plugins.getParams", {
        "index": track, "slot_index": slot, "search": "band", "max_params": 100,
    })
    if not result.get("success", False):
        raise ValueError(result.get("error") or "The EQ's bands could not be read")
    bands: dict[int, dict[str, str]] = {}
    for param in result.get("params", []):
        words = param["name"].split()  # "Band 3 freq"
        if len(words) == 3 and words[1].isdigit():
            bands.setdefault(int(words[1]), {})[words[2]] = param["value_string"]
    return bands


def pick_band(intent: dict, bands: dict[int, dict[str, str]], taken: set[int]) -> int:
    """The band for an EQ intent: one it already set (same type and frequency), else a
    flat one, in the intent's order of preference. Raises ValueError."""
    wanted_type = next(v for k, v in intent["set"].items() if k.endswith("type"))
    freq = next((v for k, v in intent["set"].items() if k.endswith("freq")), None)
    fixed = parse_display(freq) if isinstance(freq, str) else None
    order = [b for b in intent["bands"] if b not in taken and b in bands]
    for band in order:
        state = bands[band]
        if state.get("type", "").lower() != wanted_type.lower():
            continue
        shown = parse_display(state.get("freq", ""))
        if fixed is None or (shown and abs(shown[1] - fixed[1]) <= fixed[1] * SAME_FREQUENCY):
            return band
    for band in order:
        state = bands[band]
        level = parse_display(state.get("level", ""))
        if state.get("type", "").lower() in LEVEL_TYPES and level and abs(level[1]) < FLAT_DB:
            return band
    raise ValueError(
        f"No free EQ band among {intent['bands']}: they all shape the sound already"
    )


def apply_intents(track: int, names: list[str], amount: float,
                  slot: int | None = None) -> dict:
    """Apply intents that share one plugin to a track. Raises ValueError."""
    data = mixing.load()
    unknown = [n for n in names if n not in data["intents"]]
    if unknown:
        raise ValueError(f"Unknown intents {unknown}; fl_list_intents lists them")
    intents = [data["intents"][n] for n in names]
    plugins = {i["plugin"] for i in intents}
    if len(plugins) != 1:
        raise ValueError(f"These intents use different plugins: {sorted(plugins)}")
    plugin = plugins.pop()
    for intent in intents:
        mixing.settings(intent, amount, 1)  # refuse a bad amount before anything changes
    slot, added = find_or_add_plugin(track, plugin, slot)
    conn = connection.get_connection()
    applied, taken = [], set()
    for name, intent in zip(names, intents):
        band = None
        if intent.get("bands"):
            band = pick_band(intent, _bands(conn, track, slot), taken)
            taken.add(band)
        shown = {}
        for param, value in mixing.settings(intent, amount, band).items():
            index, _ = param_by_name(conn, track, slot, param)
            shown[param] = set_param_by_value(conn, track, slot, index, value)["shows"]
        applied.append({"intent": name, **({"band": band} if band else {}), "set": shown})
    return {"track": track, "slot": slot, "plugin": plugin, "added": added, "applied": applied}


def register_mixing_tools(mcp: FastMCP) -> None:
    """Register mixing intent and chain tools with the MCP server."""

    @mcp.tool()
    def fl_list_intents() -> dict:
        """List the mixing moves fl_apply_intent knows and the chains fl_setup_chain builds.

        Each intent works on one stock effect (Parametric EQ 2, Fruity Limiter,
        Reeverb 2, Delay 3); each chain is a series of those.
        """
        try:
            return mixing.describe(mixing.load())
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_apply_intent(track: int, intent: str | list[str], amount: float = 0.5,
                        slot: int | None = None) -> dict:
        """Make a musical move on a mixer track, e.g. "remove_mud", "add_air", "large_hall".

        The effect it needs is used if the track has it, else added. EQ moves take
        a free band (or the one the same move used before, so repeating it adjusts
        it). Several intents on the same effect can go in one call (a list).
        Measure the result with fl_render or fl_render_stems.

        Args:
            track: Mixer track (0 is the master).
            intent: An intent name from fl_list_intents, or a list sharing one effect.
            amount: 0 is a neutral setting, 1 the full move; 0.5 by default.
            slot: The effect slot to use, if the track has that effect more than once.

        Returns {"track", "slot", "plugin", "added", "applied": [{"intent", "band"?,
        "set": {parameter: what it shows}}]} or {"error"}.
        """
        names = [intent] if isinstance(intent, str) else list(intent)
        try:
            return apply_intents(track, names, amount, slot)
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_setup_chain(track: int, chain: str, amount: float = 0.5) -> dict:
        """Build a processing chain on a mixer track, e.g. "vocal", "lead", "drum_bus".

        Each step's effect is reused if the track has it, else added, then set
        with the step's intents (fl_list_intents shows the chains). Takes a few
        seconds per setting.

        Args:
            track: Mixer track (0 is the master).
            chain: A chain name from fl_list_intents.
            amount: How strong every move is, 0-1.

        Returns {"track", "chain", "steps": [what fl_apply_intent returns, ...]} or
        {"error", "steps" done before it}.
        """
        try:
            data = mixing.load()
        except (ValueError, RuntimeError) as e:
            return {"error": str(e)}
        if chain not in data["chains"]:
            return {"error": f"Unknown chain {chain!r}; known: {sorted(data['chains'])}"}
        steps = []
        for step in data["chains"][chain]["steps"]:
            try:
                steps.append(apply_intents(track, step, amount))
            except (ValueError, RuntimeError) as e:
                return {"error": f"Step {step}: {e}", "steps": steps}
        return {"track": track, "chain": chain, "steps": steps}
