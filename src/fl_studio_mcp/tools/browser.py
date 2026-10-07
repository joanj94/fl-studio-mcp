"""Tools that load sounds from FL Studio's browser: samples, presets, plugins and effects.

FL's API can't load anything, but it can move the browser's cursor and open the
focused item's menu, whose first entries are "Send to selected channel" and
"Open in new channel" (`utils/fl_browser.py`). Every load is confirmed by
reading the channel rack or the mixer track back.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from fl_studio_mcp.utils import browser_index, fl_browser, fl_windows
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_browser import BrowserError
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.paths import get_fl_settings_dir
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

# How long FL may take to load a plugin or sample before the result shows up.
LOAD_TIMEOUT = 10.0
POLL_INTERVAL = 0.2
WINDOW_CLOSE_DELAY = 0.3
SETTLE_SECONDS = 1.0
# What a channel is called while a sound is being loaded into it.
LOADING_NAME = "(loading)"

# Browser folders that hold plugins and their presets, with an "Effects" folder
# somewhere below for mixer effects: an effect's menu has no "Open in new channel".
PLUGIN_FOLDERS = ("plugin database", "plugin presets")
MIXER_PRESETS_FOLDER = "mixer presets"


def _is_effect(path: str) -> bool:
    parts = [part.lower() for part in fl_browser.split_path(path)]
    return bool(parts) and parts[0] in PLUGIN_FOLDERS and "effects" in parts[1:-1]


def _is_for_channels(path: str) -> bool:
    parts = [part.lower() for part in fl_browser.split_path(path)]
    return not _is_effect(path) and parts[:1] != [MIXER_PRESETS_FOLDER]


def _put_back(action: str, params: dict) -> str | None:
    """Undo a temporary change; returns what went wrong instead of raising."""
    try:
        result = call(action, params)
    except RuntimeError as e:
        return str(e)
    return result.get("error")


def _fl(action: str, params: dict | None = None) -> dict:
    """A controller command whose failure ends the load."""
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise BrowserError(str(e)) from e
    if "error" in result:
        raise BrowserError(result["error"])
    return result


def _rack() -> list[dict]:
    return _fl("channels.getAll").get("channels", [])


class Load:
    """One load. Message boxes FL shows during it are closed and kept for the report.

    Seen live: loading a plugin that isn't part of the FL Studio edition being
    tried out works (it runs as a demo), but FL says so in a message box, a
    moment after the channel has appeared. While the box is open FL ignores
    changes, so it has to go before the load can be finished.
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    def check_messages(self) -> None:
        title = fl_windows.open_message()
        if title is not None:
            fl_windows.close_messages()
            time.sleep(WINDOW_CLOSE_DELAY)
            self.messages.append(title)

    def wait_until(self, read, done):
        """Poll `read()` until `done(value)`; returns the value, or None on timeout."""
        deadline = time.monotonic() + LOAD_TIMEOUT
        while True:
            self.check_messages()
            value = read()
            if done(value):
                return value
            if time.monotonic() >= deadline:
                return None
            time.sleep(POLL_INTERVAL)

    def settle(self) -> None:
        """Give FL a moment to bring up a message box about what was just loaded."""
        deadline = time.monotonic() + SETTLE_SECONDS
        while True:
            self.check_messages()
            if self.messages or time.monotonic() >= deadline:
                return
            time.sleep(POLL_INTERVAL)

    def note(self) -> str | None:
        """What to tell about the message boxes, or None if there were none."""
        if not self.messages:
            return None
        titles = ", ".join(repr(title) for title in dict.fromkeys(self.messages))
        return (
            f"FL Studio showed a message box ({titles}) during the load; it was closed. Its "
            "text can't be read. With a title about the trial it says the plugin isn't part "
            "of the edition being tried out, where it only runs as a demo."
        )


def _close_plugin_windows() -> None:
    """Close FL's plugin windows and give them a moment to go.

    Done before a load because FL sends a browser item to the *focused plugin*
    when there is one (seen live: an effect replaced the effect whose window was
    open, or went to the mixer track of the channel whose window was), and after
    it because FL opens the window of whatever it loaded.
    """
    if fl_windows.close_all_plugin_windows():
        time.sleep(WINDOW_CLOSE_DELAY)


def open_in_new_channel(load: Load, path: str, name: str | None = None) -> dict:
    """Load a browser file into a new channel; returns the channel or raises BrowserError."""
    if not _is_for_channels(path):
        raise BrowserError(f"{path!r} is a mixer effect or preset; use fl_add_effect for effects.")
    before = _rack()
    _close_plugin_windows()
    item = fl_browser.go_to_file(path)
    fl_browser.choose_from_menu(fl_browser.OPEN_IN_NEW_CHANNEL)
    after = load.wait_until(_rack, lambda rack: len(rack) != len(before))
    load.settle()

    index = len(before)
    names = [channel.get("name") for channel in before]
    if after is None:
        raise BrowserError(f"FL Studio did not add a channel for {item.name!r}.")
    if len(after) != index + 1 or [c.get("name") for c in after[:index]] != names:
        raise BrowserError(
            f"The channel rack changed in an unexpected way while loading {item.name!r} "
            f"(it had {len(before)} channels, now {len(after)}). Check it with fl_get_all_channels."
        )
    _close_plugin_windows()
    added = _rack()[index]  # read again: a preset's plugin shows up after its channel
    result = {"index": index, "name": added.get("name"), "plugin": added.get("plugin") or "Sampler",
              "loaded": item.name}
    if name:
        _fl("channels.setName", {"index": index, "name": name})
        result["name"] = name
    return result


def send_to_channel(load: Load, path: str, channel: int, keep_name: bool) -> dict:
    """Load a browser file into an existing channel; raises BrowserError.

    FL renames the channel after what it loaded, and that is the only sign that
    it did. So the channel gets a placeholder name first: any other name
    afterwards means the load happened, even a load of the sound it already had.
    """
    if not _is_for_channels(path):
        raise BrowserError(f"{path!r} is a mixer effect or preset; use fl_add_effect for effects.")
    before = _rack()
    if not 0 <= channel < len(before):
        raise BrowserError(f"There is no channel {channel}.")
    old_name = before[channel].get("name", "")
    _close_plugin_windows()
    _fl("channels.selectOne", {"index": channel})
    item = fl_browser.go_to_file(path)  # after selecting: this leaves the browser focused
    _fl("channels.setName", {"index": channel, "name": LOADING_NAME})
    after = None
    try:
        fl_browser.choose_from_menu(fl_browser.SEND_TO_SELECTED_CHANNEL)
        after = load.wait_until(_rack, lambda rack: len(rack) != len(before)
                                or rack[channel].get("name") != LOADING_NAME)
        load.settle()
    finally:
        # Whatever went wrong, the placeholder must not stay: roles go by name.
        if after is None or after[channel].get("name") == LOADING_NAME:
            _put_back("channels.setName", {"index": channel, "name": old_name})
    if after is None:
        raise BrowserError(f"FL Studio did not load {item.name!r} into channel {channel}.")
    if len(after) != len(before):
        raise BrowserError(
            f"The channel rack changed in an unexpected way while loading {item.name!r}. "
            "Check it with fl_get_all_channels."
        )
    _close_plugin_windows()
    result = {"index": channel, "name": after[channel].get("name"),
              "plugin": after[channel].get("plugin") or "Sampler", "loaded": item.name}
    if keep_name and old_name:
        _fl("channels.setName", {"index": channel, "name": old_name})
        result["name"] = old_name
    return result


def _effects(track: int) -> list[dict]:
    return _fl("mixer.getTrackEffects", {"track": track}).get("effects", [])


def add_effect(load: Load, path: str, track: int) -> dict:
    """Load an effect from the browser onto a mixer track; raises BrowserError.

    Seen live: with no plugin window open, FL puts the effect on the mixer track
    of the *selected channel* (selecting a mixer track changes nothing). So a
    channel routed to the track is selected, or one is routed there for the
    moment and routed back.
    """
    if not _is_effect(path):
        raise BrowserError(
            "An effect must come from 'Plugin database/Effects/...' or "
            "'Plugin presets/Effects/...'. fl_browser_list shows them."
        )
    before = _effects(track)
    rack = _rack()
    if not rack:
        raise BrowserError("The channel rack is empty; an effect needs a channel to aim with.")
    routed = [c["index"] for c in rack if c.get("target_fx_track") == track]
    carrier = routed[0] if routed else 0
    home = rack[carrier].get("target_fx_track", 0)
    selected = [c["index"] for c in rack if c.get("is_selected")]
    _close_plugin_windows()
    problems: list[str] = []
    try:
        if not routed:
            _fl("channels.routeToMixer", {"channel_index": carrier, "mixer_track": track})
        _fl("channels.selectOne", {"index": carrier})
        item = fl_browser.go_to_file(path)  # after selecting: this leaves the browser focused
        fl_browser.choose_from_menu(fl_browser.SEND_TO_SELECTED_CHANNEL)
        after = load.wait_until(lambda: _effects(track),
                                lambda found: len(found) > len(before))
        load.settle()
    finally:
        if not routed:
            failed = _put_back("channels.routeToMixer",
                               {"channel_index": carrier, "mixer_track": home})
            if failed:
                problems.append(f"Channel {carrier} was routed to mixer track {track} to aim "
                                f"the effect and could not be routed back to {home}: {failed}")
        if selected and _put_back("channels.selectOne", {"index": selected[0]}):
            problems.append("The channel selection could not be put back.")

    taken = {effect["slot"] for effect in before}
    new = next((effect for effect in after or [] if effect["slot"] not in taken), None)
    if new is None:
        raise BrowserError(" ".join([
            f"FL Studio did not add {item.name!r} to mixer track {track}. Its effect slots may "
            "be full, or the effect went elsewhere: check with fl_get_track_effects.", *problems,
        ]))
    _close_plugin_windows()
    result = {"track": track, "slot": new["slot"], "plugin": new["plugin"], "effects": after}
    if problems:
        result["warning"] = " ".join(problems)
    return result


def _close_menus_left_open() -> None:
    """Close a menu FL left open without acting on the choice made in it.

    Right after Enter a menu counts as closed while it fades, so it is only
    seen here, once the load has been waited for. Never raises.
    """
    try:
        fl_windows.close_popup_menus()
    except Exception:
        logger.exception("Could not close FL Studio's menus")


def run_load(action, *args) -> dict:
    """Run a load with the lock held from its first read to its clean-up.

    Returns the load's result or {"error": ...}; either one tells about message
    boxes FL showed on the way.
    """
    load = Load()
    try:
        with keyboard_lock:
            result = action(load, *args)
    except BrowserError as e:
        _close_menus_left_open()
        return {"error": " ".join(filter(None, [str(e), load.note()]))}
    except Exception as e:  # e.g. Windows refusing a call: a tool answers, it doesn't raise
        logger.exception("A load failed unexpectedly")
        _close_menus_left_open()
        return {"error": " ".join(filter(None, [f"The load failed: {e!r}.", load.note()]))}
    note = load.note()
    if note:
        result["warning"] = " ".join(filter(None, [result.get("warning"), note]))
    return result


def _locked(command, *args) -> dict:
    """Run a controller command under the lock; {"error": ...} if FL can't."""
    try:
        with keyboard_lock:
            return command(*args)
    except BrowserError as e:
        return {"error": str(e)}


def register_browser_tools(mcp: FastMCP) -> None:
    """Register browser and loading tools with the MCP server."""

    @mcp.tool()
    def fl_browser_list(path: str = "") -> dict:
        """List a folder of FL Studio's browser: the sounds and plugins that can be loaded.

        Useful folders: "Packs" (stock samples, e.g. "Packs/Drums/Kicks"),
        "Plugin database/Generators" and "Plugin database/Effects" (plugins by
        category), "Channel presets" and "Plugin presets/Generators" (instrument
        presets by plugin), and any folder the user added to the browser.
        Moves the browser's cursor and closes the folders it passes.

        Args:
            path: Folder names from the browser's top level, separated by "/".
                "" lists the top level.

        Returns {"path", "items": [{"name", "kind": "folder"|"file"}]} or {"error"}.
        """
        try:
            with keyboard_lock:
                items = fl_browser.list_folder(path)
        except BrowserError as e:
            return {"error": str(e)}
        return {"path": "/".join(fl_browser.split_path(path)),
                "items": [item.to_dict() for item in items]}

    @mcp.tool()
    def fl_browser_search(query: str, folder: str = "", limit: int = 40) -> dict:
        """Find sounds by name in FL Studio's browser: samples and presets, in any folder.

        Much faster than listing folders one by one. Searches the folders the
        browser takes from disk: FL's own ("Packs", "Plugin presets", "Channel
        presets", "Plugin database", ...) and the user's presets and project
        bones. Doesn't touch FL Studio. A folder the user added to the browser
        from elsewhere isn't covered: list it with fl_browser_list.

        Args:
            query: Words that must all be in the path, any case and order:
                "kick 909", "sytrus pad dark", "riser noise".
            folder: Only search below this browser folder, e.g. "Packs/Drums".
            limit: How many paths to return at most.

        Returns {"matches": [browser paths, best first], "total"} or {"error"}.
        The paths go straight into fl_load_in_new_channel, fl_load_in_channel
        and fl_add_effect.
        """
        if isinstance(limit, bool) or not 1 <= limit <= browser_index.MAX_LIMIT:
            return {"error": f"limit must be 1-{browser_index.MAX_LIMIT}, got {limit!r}"}
        try:
            install_dir = fl_windows.fl_install_dir()
        except Exception:  # not on Windows, or FL isn't running: the user's folders remain
            install_dir = None
        try:
            roots = browser_index.browser_roots(install_dir, get_fl_settings_dir().parent)
            matches, total = browser_index.search(roots, query, folder, limit)
        except (ValueError, OSError) as e:
            return {"error": str(e)}
        result: dict = {"matches": matches, "total": total}
        if install_dir is None:
            result["warning"] = (
                "FL Studio's own folders were not searched: it must be running (on "
                "Windows) for its install folder to be found."
            )
        return result

    @mcp.tool()
    def fl_load_in_new_channel(path: str, name: str | None = None) -> dict:
        """Add a channel from a browser file: a sample, an instrument preset or a plugin.

        A sample (.wav ...) becomes a Sampler channel playing it; a preset
        (.fst under "Channel presets" or "Plugin presets/Generators", or a
        Morphine .mrp) loads its plugin with that sound; a file under
        "Plugin database/Generators" loads the plugin with its default sound.
        The channel lands at the end of the rack, not routed to a mixer track
        (see fl_route_channel_to_mixer).
        Windows only. FL Studio needn't be in front.

        Args:
            path: Browser path of the file, as listed by fl_browser_list, e.g.
                "Packs/Drums/Kicks/909 Kick" (the extension may be left out).
            name: Name for the new channel (a role such as "Kick"). Default:
                the file's name.

        Returns {"index", "name", "plugin", "loaded"} or {"error": ...}.
        """
        return run_load(open_in_new_channel, path, name)

    @mcp.tool()
    def fl_load_in_channel(path: str, channel: int | str, keep_name: bool = True) -> dict:
        """Replace an existing channel's sound with a browser file (sample or preset).

        A sample replaces the sample of a Sampler channel; a preset replaces the
        channel's plugin and sound. Windows only. FL Studio needn't be in front.

        Args:
            path: Browser path of the file, as listed by fl_browser_list.
            channel: Channel index or role (e.g. "kick").
            keep_name: Keep the channel's name (FL would rename it after the
                file, which breaks roles). Default True.

        Returns {"index", "name", "plugin", "loaded"} or {"error": ...}.
        """
        try:
            index = resolve_channel(channel)
        except ValueError as e:
            return {"error": str(e)}
        return run_load(send_to_channel, path, index, keep_name)

    @mcp.tool()
    def fl_add_effect(path: str, track: int) -> dict:
        """Add an effect plugin to a mixer track, in its first free slot.

        Windows only. FL Studio needn't be in front. Set the effect's controls
        afterwards with fl_get_plugin_params / fl_set_plugin_param_value using
        the returned slot.

        Args:
            path: Browser path of the effect, e.g.
                "Plugin database/Effects/Dynamics/Fruity Limiter", or a preset
                under "Plugin presets/Effects".
            track: Mixer track number (0 is the master).

        Returns {"track", "slot", "plugin", "effects"} or {"error": ...}.
        """
        return run_load(add_effect, path, track)

    @mcp.tool()
    def fl_get_track_effects(track: int) -> dict:
        """List the effect plugins on a mixer track.

        Args:
            track: Mixer track number (0 is the master).

        Returns {"track", "name", "effects": [{"slot", "plugin", "enabled",
        "mix"}]} or {"error"}; mix is how much of the effect is heard, 0 to 1.
        """
        return _locked(_fl, "mixer.getTrackEffects", {"track": track})

    @mcp.tool()
    def fl_set_effect(
        track: int, slot: int, enabled: bool | None = None, mix: float | None = None
    ) -> dict:
        """Switch an effect on a mixer track on or off, or set how much of it is mixed in.

        Effects can't be removed, so switching one off is the way to take it
        out of the sound. Its own controls are set with
        fl_set_plugin_param_value (plugin_index = the track, slot_index = the slot).

        Args:
            track: Mixer track number (0 is the master).
            slot: Effect slot, as listed by fl_get_track_effects.
            enabled: True switches the effect on, False off. Default: unchanged.
            mix: 0.0 (dry, effect not heard) to 1.0 (fully applied). Default: unchanged.

        Returns {"track", "effects": [...]} or {"error"}.
        """
        if enabled is None and mix is None:
            return {"error": "Give enabled and/or mix."}
        return _locked(_fl, "mixer.setEffect",
                       {"track": track, "slot": slot, "enabled": enabled, "mix": mix})

    @mcp.tool()
    def fl_close_message() -> dict:
        """Close a message box FL Studio is showing, as its X button would (Windows).

        While a message box is open FL ignores changes, and tools that change
        something say so. Its text can't be read, only its title. Closing a
        question this way answers "cancel".

        Returns {"closed": number of message boxes, "title": the first one's title}.
        """
        title = fl_windows.open_message()
        if title is None:
            return {"closed": 0, "title": None}
        with keyboard_lock:
            return {"closed": fl_windows.close_messages(), "title": title}

    @mcp.tool()
    def fl_undo(steps: int = 1) -> dict:
        """Undo the last changes in FL Studio, like Ctrl+Alt+Z.

        This is the way to take back a channel or effect that was just added:
        the API can't delete them. Check the result afterwards (for example
        with fl_get_all_channels): FL decides what counts as one step.

        Args:
            steps: How many steps to go back (1-50).

        Returns {"undone", "history_position", "history_count"} or {"error"}.
        """
        return _locked(_fl, "project.undo", {"steps": steps})
