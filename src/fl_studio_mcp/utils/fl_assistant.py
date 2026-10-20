"""Channels and effects through FL Studio 2026's own assistant tools (utils/gopher.py).

FL's tools add a channel or an effect by plugin name and remove channels and effects,
with no menu, browser walk or confirm box. FL's answer only says what it meant to do,
so every change is checked here by reading back over the controller, like the browser
loaders do. Indexes here are ours (global channel index, mixer slot 0-9); FL's tools
count channels and slots from 1.
"""

from __future__ import annotations

import time

from fl_studio_mcp.utils import gopher
from fl_studio_mcp.utils.connection import call

MIXER_SLOTS = 10
# How long a change may take to show in the controller's reading.
SETTLE_TIMEOUT = 2.0
POLL_INTERVAL = 0.05


class AssistantError(Exception):
    """FL's assistant tools refused a change or the change didn't happen."""


class UnexpectedChange(AssistantError):
    """FL changed something, but not what was asked: don't retry another way."""


def available() -> bool:
    """True when FL's assistant tools can be reached (FL started with fl_launch)."""
    return gopher.page_url() is not None


def _tool(name: str, arguments: dict) -> str | None:
    """Run one of FL's tools; returns what went wrong, or None.

    No exception: FL may have made the change even when its answer was lost, so the
    callers always read the state back and decide from that.
    """
    try:
        gopher.call_tool(name, arguments)
        return None
    except gopher.GopherError as e:
        return str(e)


def _nothing_changed(message: str, failure: str | None) -> AssistantError:
    return AssistantError(f"{message} ({failure})" if failure else message)


def _read(action: str, params: dict | None = None) -> dict:
    result = call(action, params or {})
    if "error" in result:
        raise AssistantError(result["error"])
    return result


def _rack() -> list[str]:
    return [c["name"] for c in _read("channels.getAll")["channels"]]


def _effects(track: int) -> dict[int, str]:
    effects = _read("mixer.getTrackEffects", {"track": track})["effects"]
    return {e["slot"]: e["plugin"] for e in effects}


def _wait_for(read, done):
    """Read until `done(value)` holds or SETTLE_TIMEOUT passes; returns the last value read."""
    deadline = time.monotonic() + SETTLE_TIMEOUT
    value = read()
    while not done(value) and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        value = read()
    return value


def add_channel(plugin: str) -> dict:
    """Add a channel with an instrument plugin at the end of the rack (FL's add_channel)."""
    before = _rack()
    failure = _tool("add_channel", {"plugin": plugin})
    after = _wait_for(_rack, lambda rack: len(rack) > len(before))
    if after == before:
        raise _nothing_changed(
            f"FL Studio added no channel for '{plugin}' (is it in the plugin database?)",
            failure)
    if len(after) != len(before) + 1 or after[:len(before)] != before:
        raise UnexpectedChange(
            f"The channel rack changed in an unexpected way while adding '{plugin}' "
            f"(it had {len(before)} channels, now {len(after)}). Check it with "
            "fl_get_all_channels.")
    index = len(after) - 1
    return {"index": index, "name": after[index], "plugin": plugin}


def add_effect(plugin: str, track: int, slot: int | None = None) -> dict:
    """Put an effect plugin into a mixer track's slot (default: the first free one)."""
    before = _effects(track)
    if slot is None:
        free = [s for s in range(MIXER_SLOTS) if s not in before]
        if not free:
            raise AssistantError(f"Mixer track {track} has no free effect slot")
        slot = free[0]
    elif not 0 <= slot < MIXER_SLOTS:
        raise AssistantError(f"slot must be 0-{MIXER_SLOTS - 1}")
    elif slot in before:
        raise AssistantError(
            f"Mixer track {track} already has {before[slot]} in slot {slot}: delete it first")
    failure = _tool("add_effect", {"plugin": plugin, "target_tracks": str(track),
                                   "slot_number": slot + 1})
    after = _wait_for(lambda: _effects(track), lambda effects: slot in effects)
    if after == before:
        raise _nothing_changed(
            f"'{plugin}' didn't appear on mixer track {track} (is it an effect in the "
            "plugin database?)", failure)
    if {s: p for s, p in after.items() if s != slot} != before or slot not in after:
        raise UnexpectedChange(
            f"Mixer track {track}'s effects changed in an unexpected way while adding "
            f"'{plugin}': {before} became {after}. Check with fl_get_track_effects.")
    return {"track": track, "slot": slot, "plugin": after[slot],
            "effects": [{"slot": s, "plugin": p} for s, p in sorted(after.items())]}


def delete_effect(track: int, slot: int) -> str:
    """Remove the effect in a mixer track's slot; returns its plugin's name."""
    before = _effects(track)
    if slot not in before:
        raise AssistantError(f"Mixer track {track} has no effect in slot {slot}")
    failure = _tool("remove_effect", {"target_tracks": str(track), "slot_numbers": str(slot + 1)})
    after = _wait_for(lambda: _effects(track), lambda effects: slot not in effects)
    if after == before:
        raise _nothing_changed(
            f"The effect in slot {slot} of mixer track {track} is still there", failure)
    if after != {s: p for s, p in before.items() if s != slot}:
        raise UnexpectedChange(
            f"Mixer track {track}'s effects changed in an unexpected way: {before} became "
            f"{after}. Check with fl_get_track_effects.")
    return before[slot]


def delete_channels(indexes: list[int]) -> int:
    """Remove channels by global index; returns how many channels remain.

    FL's tool counts channels as the rack shows them, from 1. The rack left must be
    exactly the old one without these channels, or this raises (undo with fl_undo).
    """
    before = _rack()
    expected = [n for i, n in enumerate(before) if i not in set(indexes)]
    failure = _tool("remove_channels",
                    {"target_channels": ",".join(str(i + 1) for i in sorted(indexes))})
    after = _wait_for(_rack, lambda rack: len(rack) <= len(expected))
    if after == before:
        raise _nothing_changed(f"FL Studio removed none of the channels {indexes}", failure)
    if after != expected:
        raise UnexpectedChange(
            f"FL Studio removed other channels than asked: the rack was {before}, "
            f"is now {after}; fl_undo takes it back")
    return len(after)
