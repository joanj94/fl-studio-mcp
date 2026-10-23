"""Effect chains kept under a name and put on any mixer track: a track template.

`fl_save_chain` reads a track's effects with every parameter (`project.getSnapshot`)
and keeps them as JSON in the controller's folder. `fl_apply_chain` loads the same
plugins onto a track, in order, into its free slots (FL's own tools first, else the
browser, like fl_add_effect), then sets their on/off, mix and parameters in one
command (`project.applyState`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.browser import (
    _is_effect,
    _native_plugin,
    add_effect,
    add_effect_natively,
    run_load,
    run_load_natively,
)
from fl_studio_mcp.tools.snapshots import NAME_PATTERN, _long_call
from fl_studio_mcp.utils.paths import atomic_write_json, get_hardware_dir
from fl_studio_mcp.utils.plugin_database import effect_browser_path

if TYPE_CHECKING:
    from pathlib import Path

    from fastmcp import FastMCP

CHAIN_FOLDER = "chains"
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                  *(f"LPT{i}" for i in range(1, 10))}
KEPT = ("plugin", "enabled", "mix", "params", "params_truncated")


def _chain_path(name: str) -> Path:
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or name.strip(". ") != name:
        raise ValueError("A chain name is 1-64 letters, digits, spaces, '.', '-' or '_', "
                         f"not starting or ending with a space or dot; got {name!r}")
    if name.split(".")[0].strip().upper() in RESERVED_NAMES:
        raise ValueError(f"{name!r} is a name Windows keeps for a device: choose another")
    return get_hardware_dir() / CHAIN_FOLDER / f"{name}.json"


def _track_state(track: int) -> dict:
    state = _long_call("project.getSnapshot", {"params": True})
    if "error" in state:
        raise ValueError(state["error"])
    found = [t for t in state["tracks"] if t["index"] == track]
    if not found:
        raise ValueError(f"Mixer track {track} isn't in use")
    return found[0]


def _load(plugin: str, track: int) -> dict:
    path = effect_browser_path(plugin)
    native = _native_plugin(path)
    if native is None or not _is_effect(path):
        return run_load(add_effect, path, track)
    return run_load_natively(lambda: run_load(add_effect_natively, native, track),
                             lambda: run_load(add_effect, path, track))


def save_chain(track: int, name: str) -> dict:
    path = _chain_path(name)
    state = _track_state(track)
    effects = [{key: effect[key] for key in ("slot", *KEPT) if key in effect}
               for effect in state.get("effects", [])]
    if not effects:
        raise ValueError(f"Mixer track {track} has no effects to keep")
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"name": name, "saved": datetime.now().isoformat(timespec="seconds"),
                             "track": track, "track_name": state.get("name"),
                             "effects": effects})
    return {"name": name, "path": str(path), "effects": [e["plugin"] for e in effects]}


def apply_chain(track: int, name: str) -> dict:
    path = _chain_path(name)
    try:
        chain = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(f"No chain named {name!r}; fl_list_chains lists them") from None
    loaded, problems = [], []
    for effect in chain["effects"]:
        result = _load(effect["plugin"], track)
        if "error" in result:
            problems.append(f"{effect['plugin']}: {result['error']}")
            break  # the rest would land in the wrong order
        # FL names a plugin it added itself in other capitals ("Fruity parametric EQ 2").
        loaded.append({**{k: effect[k] for k in KEPT if k in effect},
                       "slot": result["slot"], "plugin": result.get("plugin", effect["plugin"])})
    if loaded:
        applied = _long_call("project.applyState",
                             {"tracks": [{"index": track, "effects": loaded}], "channels": []})
        if "error" in applied:
            problems.append(f"The settings could not be put back: {applied['error']}")
        else:
            problems += applied.get("skipped", [])
    result = {"track": track, "chain": name,
              "loaded": [{"slot": e["slot"], "plugin": e["plugin"]} for e in loaded]}
    if problems:
        result["error" if len(loaded) < len(chain["effects"]) else "warning"] = "; ".join(problems)
    return result


def register_chain_tools(mcp: FastMCP) -> None:
    """Register the saved effect chain tools with the MCP server."""

    @mcp.tool()
    def fl_save_chain(track: int, name: str) -> dict:
        """Keep a mixer track's effect chain under a name: plugins, order, on/off, mix
        and every parameter (up to 1024 each). A chain of the same name is replaced.

        Args:
            track: Mixer track (0 = master).
            name: e.g. "kick bus", "vocal".

        Returns {"name", "path", "effects": [plugin names]} or {"error"}.
        """
        try:
            return save_chain(track, name)
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_apply_chain(track: int, name: str) -> dict:
        """Put a kept chain on a mixer track: its plugins are loaded after the track's
        own effects, in order, and set as they were kept. Windows only.

        Plugins are loaded by name from FL's plugin database, so a chain only moves
        between projects on a machine that has them. Stops at the first plugin
        that can't be loaded (the rest would land out of order); fl_undo or
        fl_delete_effect takes back what was added.

        Returns {"track", "chain", "loaded": [{"slot", "plugin"}], "warning"?}
        or {"error"}.
        """
        try:
            return apply_chain(track, name)
        except (ValueError, OSError, KeyError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_list_chains() -> dict:
        """The kept effect chains: {"chains": [{"name", "saved", "effects"}]}."""
        folder = get_hardware_dir() / CHAIN_FOLDER
        chains = []
        for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                chains.append({"name": data["name"], "saved": data.get("saved"),
                               "effects": [e["plugin"] for e in data["effects"]]})
            except (OSError, ValueError, KeyError):
                chains.append({"name": path.stem, "error": "unreadable"})
        return {"chains": chains}
