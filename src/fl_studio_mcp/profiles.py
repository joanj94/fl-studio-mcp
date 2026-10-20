"""Tool profiles: which groups of tools the server offers.

Some clients cope badly with 160-odd tools. `FL_MCP_PROFILE` names one or more
profiles (comma-separated, e.g. "compose,mix"); the server registers the tool groups
of all of them. Unset or "all" registers everything. The connection tools
(fl_connect, fl_connection_status) and fl_batch are always there.

fl_batch runs several of the registered tools in one call (one round trip for the
client), in order, stopping at the first error unless told not to.
"""

from __future__ import annotations

import inspect
import logging
import os
from collections.abc import Callable
from typing import Any

from fl_studio_mcp import tools

logger = logging.getLogger(__name__)

ENV_VAR = "FL_MCP_PROFILE"
MAX_BATCH = 50

GROUPS: dict[str, Callable] = {
    "transport": tools.register_transport_tools,
    "mixer": tools.register_mixer_tools,
    "groups": tools.register_group_tools,
    "mixing": tools.register_mixing_tools,
    "mix_check": tools.register_mix_check_tools,
    "channels": tools.register_channel_tools,
    "channel_setup": tools.register_channel_setup_tools,
    "browser": tools.register_browser_tools,
    "plugins": tools.register_plugin_tools,
    "piano_roll": tools.register_piano_roll_tools,
    "piano_roll_markers": tools.register_piano_roll_marker_tools,
    "arrange": tools.register_arrange_tools,
    "assistant": tools.register_assistant_tools,
    "audio": tools.register_audio_tools,
    "automation": tools.register_automation_tools,
    "music": tools.register_music_tools,
    "project": tools.register_project_tools,
    "patterns": tools.register_pattern_tools,
    "roles": tools.register_roles_tools,
    "styles": tools.register_style_tools,
    "tuning": tools.register_tuning_tools,
    "vocals": tools.register_vocal_tools,
    "screen": tools.register_screen_tools,
    "snapshots": tools.register_snapshot_tools,
    "chains": tools.register_chain_tools,
    "stems": tools.register_stem_tools,
}

_BASICS = ("transport", "project", "patterns", "channels", "roles", "screen")

PROFILES: dict[str, tuple[str, ...]] = {
    "all": tuple(GROUPS),
    # Basic control only: play, tempo, channels, mixer levels and notes.
    "minimal": (*_BASICS, "mixer", "piano_roll"),
    # Writing a song: sounds, notes, theory, styles, the playlist, tuning, vocals.
    "compose": (*_BASICS, "browser", "channel_setup", "plugins", "piano_roll",
                "piano_roll_markers", "music", "arrange", "styles", "tuning", "vocals",
                "assistant", "stems"),
    # Balancing and checking a song: mixer, effects, renders, automation, snapshots.
    "mix": (*_BASICS, "mixer", "groups", "mixing", "mix_check", "plugins", "browser",
            "audio", "automation", "snapshots", "chains", "stems"),
    # Measuring audio files and reading music, mostly without FL.
    "analysis": ("project", "screen", "audio", "music", "styles", "stems"),
}


def groups_for(value: str | None) -> list[str]:
    """Tool groups for a profile setting, in registration order; ValueError if unknown."""
    names = [part.strip().lower() for part in (value or "all").split(",") if part.strip()]
    unknown = [name for name in names if name not in PROFILES]
    if unknown:
        raise ValueError(
            f"Unknown {ENV_VAR} {', '.join(unknown)}: choose from {', '.join(PROFILES)}")
    wanted = {group for name in names or ["all"] for group in PROFILES[name]}
    return [group for group in GROUPS if group in wanted]


class _Recorder:
    """Passes @mcp.tool() through to the server and keeps each function for fl_batch."""

    def __init__(self, mcp, functions: dict[str, Callable]) -> None:
        self._mcp = mcp
        self._functions = functions

    def tool(self, *args: Any, **kwargs: Any) -> Callable:
        register = self._mcp.tool(*args, **kwargs)

        def record(fn: Callable) -> Callable:
            self._functions[fn.__name__] = fn
            return register(fn)

        return record


def _plain(result: Any) -> Any:
    """A tool's result as JSON-friendly data (a picture can't travel inside a batch)."""
    if isinstance(result, (dict, list, str, int, float, bool)) or result is None:
        return result
    return {"error": f"{type(result).__name__} results can't be part of a batch: "
                     "call that tool on its own"}


# The older tools answer with a sentence; these openings are their failures.
_FAILED_TEXT = ("Error", "Failed", "Could not")


def _failed(result: Any) -> bool:
    if isinstance(result, str):
        return result.startswith(_FAILED_TEXT)
    return isinstance(result, dict) and "error" in result and not result.get("success", False)


def run_batch(functions: dict[str, Callable], calls: list[dict],
              stop_on_error: bool = True) -> dict:
    """Run [{"tool", "args"}] in order; {"results": [{"tool", "result"}], "completed"}."""
    if not isinstance(calls, list) or not 1 <= len(calls) <= MAX_BATCH:
        return {"error": f"calls must be a list of 1-{MAX_BATCH} calls"}
    for number, call in enumerate(calls, 1):
        tool = call.get("tool") if isinstance(call, dict) else None
        if not isinstance(tool, str) or tool not in functions:
            return {"error": f"Call {number}: unknown or missing tool "
                             f"{call.get('tool') if isinstance(call, dict) else call!r}"}
        if not isinstance(call.get("args", {}), dict):
            return {"error": f"Call {number}: args must be an object"}
    results = []
    for call in calls:
        function, args = functions[call["tool"]], call.get("args", {})
        try:
            inspect.signature(function).bind(**args)
        except TypeError as e:  # wrong or missing argument names
            result = {"error": f"Wrong arguments: {e}"}
        else:
            try:
                result = _plain(function(**args))
            except Exception as e:  # a tool should answer; one that raises still ends here
                logger.exception("%s raised inside a batch", call["tool"])
                result = {"error": f"{call['tool']} failed: {type(e).__name__}: {e}"}
        results.append({"tool": call["tool"], "result": result})
        if stop_on_error and _failed(result):
            break
    return {"results": results, "completed": len(results) == len(calls)
            and not any(_failed(r["result"]) for r in results)}


def register_profile(mcp, value: str | None = None) -> list[str]:
    """Register the tool groups of the profile(s) in `value` (default: the env var),
    and fl_batch over them."""
    chosen = groups_for(os.environ.get(ENV_VAR) if value is None else value)
    functions: dict[str, Callable] = {}
    recorder = _Recorder(mcp, functions)
    for group in chosen:
        GROUPS[group](recorder)

    @mcp.tool()
    def fl_batch(calls: list[dict], stop_on_error: bool = True) -> dict:
        """Run several of this server's tools in one call, in order.

        Saves round trips for a run of small steps (set tempo, name channels,
        write steps, set levels...). Each call is {"tool": name, "args": {...}},
        e.g. [{"tool": "fl_set_tempo", "args": {"bpm": 150}}, {"tool":
        "fl_set_channel_name", "args": {"index": 0, "name": "Kick"}}]. Stops at
        the first result with an "error" unless stop_on_error is false. Nothing
        is undone when a later call fails. At most 50 calls; fl_screenshot's
        picture can't come back through a batch.

        Returns {"results": [{"tool", "result"}], "completed"} or {"error"}.
        """
        return run_batch(functions, calls, stop_on_error)

    return chosen
