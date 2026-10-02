# name=FL Studio MCP Controller
# url=https://github.com/karl-andres/fl-studio-mcp
# supportedDevices=FL Studio MCP

"""
FL Studio MIDI Controller Script for MCP Integration.

This script runs inside FL Studio and receives MIDI trigger messages from the
MCP server. When triggered, it reads a command from a JSON file, executes the
corresponding FL Studio API function, and writes the response to another JSON file.

Communication flow:
1. MCP server writes command to mcp_command.json
2. MCP server sends MIDI note 127 (trigger)
3. This script receives the trigger via OnMidiMsg()
4. Script reads command JSON, executes FL Studio API
5. Script writes response to mcp_response.json
6. MCP server reads response
"""

import json
import os
import sys
import time
from pathlib import Path

# FL Studio API modules (available when running inside FL Studio)
import channels
import general
import midi
import mixer
import patterns
import playlist
import plugins
import transport
import ui


def _get_documents_dir() -> Path:
    """The user's Documents folder, following OneDrive/folder redirection on Windows.

    Mirrors fl_studio_mcp.utils.paths.get_documents_dir on the server side.
    FL's embedded Python may lack ctypes, so any failure falls back to the default.
    """
    if sys.platform == "win32":
        try:
            import ctypes

            buffer = ctypes.create_unicode_buffer(260)
            # 5 = CSIDL_PERSONAL (Documents)
            result = ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer)
            if result == 0 and buffer.value:
                return Path(buffer.value)
        except Exception:
            pass  # Fall through to the default location below.
        return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents"
    return Path.home() / "Documents"


def _get_script_dir() -> Path:
    """Get the script directory path.

    FL Studio's Python environment doesn't support __file__, so we construct
    the path based on the platform's standard FL Studio settings location.
    """
    settings = _get_documents_dir() / "Image-Line" / "FL Studio" / "Settings"
    return settings / "Hardware" / "FLStudioMCP"


# File paths for JSON communication
SCRIPT_DIR = _get_script_dir()
COMMAND_FILE = SCRIPT_DIR / "mcp_command.json"
RESPONSE_FILE = SCRIPT_DIR / "mcp_response.json"

# MIDI trigger note
TRIGGER_NOTE = 127


def OnInit():
    """Called when the script is loaded."""
    print("FL Studio MCP Controller initialized")
    print(f"Command file: {COMMAND_FILE}")
    print(f"Response file: {RESPONSE_FILE}")


def OnDeInit():
    """Called when the script is unloaded."""
    print("FL Studio MCP Controller deinitialized")


def OnMidiMsg(event):
    """Called when a MIDI message is received."""
    # Check for trigger note (Note On, note 127)
    if event.midiId == 0x90 and event.data1 == TRIGGER_NOTE and event.data2 > 0:
        execute_pending_command()
        event.handled = True


def OnIdle():
    """Called periodically when FL Studio is idle."""
    pass  # Required FL Studio API hook; no polling needed


def execute_pending_command():
    """Read command from JSON file, execute it, and write response.

    The response echoes the command's "id" so the server can tell it apart
    from a stale response to an earlier command.
    """
    request_id = None
    response = {"success": False, "error": None}

    try:
        # Read command file
        if not COMMAND_FILE.exists():
            response["error"] = "No command file found"
            write_response(response, request_id)
            return

        command_text = COMMAND_FILE.read_text()
        command = json.loads(command_text)

        request_id = command.get("id")
        action = command.get("action", "")
        params = command.get("params", {})

        # Execute command and get result; handlers signal failure with an "error" key
        result = dispatch_command(action, params)
        response = {"success": "error" not in result, **result}

    except json.JSONDecodeError as e:
        response["error"] = f"Invalid JSON in command file: {e}"
    except Exception as e:
        response["error"] = f"Error executing command: {e}"

    write_response(response, request_id)


def write_response(response: dict, request_id=None):
    """Write response to JSON file, tagged with the request id.

    Always writes something: if the result can't be serialized, an error
    response is written instead so the server doesn't wait until its timeout.

    The file is written in place, never via temp file + os.replace: in FL Studio
    2026, os.replace returns NULL without setting an exception, which leaves the
    embedded interpreter failing every later call. Writing in place is safe
    because the server skips JSON it can't parse and only accepts a response
    carrying its own request id.
    """
    tagged = {**response, "id": request_id}
    try:
        text = json.dumps(tagged, indent=2)
    except (TypeError, ValueError) as e:
        text = json.dumps({
            "success": False,
            "id": request_id,
            "error": f"Could not serialize FL Studio result: {e}",
        })

    try:
        RESPONSE_FILE.write_text(text)
    except Exception as e:
        print(f"Error writing response: {e}")


def handle_dev_reload_script() -> dict:
    """Re-run this script's installed file so edits apply without FL's Reload button.

    The source is compiled first, so a syntax error changes nothing. Functions
    and constants are then replaced in place, and the next command uses the new
    code. If the file raises while running, the names defined before the error
    are already replaced: fix the file and reload again, or use FL's Reload.
    """
    path = SCRIPT_DIR / "device_FLStudioMCP.py"
    try:
        code = compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except SyntaxError as e:
        return {"error": "SyntaxError in %s: %s" % (path, e)}
    try:
        exec(code, globals())
    except Exception as e:
        return {"error": "Reload of %s partly applied, then failed: %r. "
                         "Fix the file and reload again." % (path, e)}
    return {"reloaded": str(path)}


def dispatch_command(action: str, params: dict) -> dict:
    """Route command to appropriate handler and return result."""

    # Transport commands
    if action == "dev.reloadScript":
        return handle_dev_reload_script()
    if action == "transport.start":
        return handle_transport_start()
    elif action == "transport.stop":
        return handle_transport_stop()
    elif action == "transport.record":
        return handle_transport_record()
    elif action == "transport.getStatus":
        return handle_transport_get_status()
    elif action == "transport.setPosition":
        return handle_transport_set_position(params)
    elif action == "transport.getLength":
        return handle_transport_get_length()
    elif action == "transport.setLoopMode":
        return handle_transport_set_loop_mode(params)
    elif action == "transport.setPlaybackSpeed":
        return handle_transport_set_playback_speed(params)

    # Mixer commands
    elif action == "mixer.getTrackCount":
        return handle_mixer_get_track_count()
    elif action == "mixer.getTrackInfo":
        return handle_mixer_get_track_info(params)
    elif action == "mixer.getAllTracks":
        return handle_mixer_get_all_tracks(params)
    elif action == "mixer.setTrackVolume":
        return handle_mixer_set_track_volume(params)
    elif action == "mixer.setTrackVolumeDb":
        return handle_mixer_set_track_volume_db(params)
    elif action == "mixer.setTrackPan":
        return handle_mixer_set_track_pan(params)
    elif action == "mixer.muteTrack":
        return handle_mixer_mute_track(params)
    elif action == "mixer.soloTrack":
        return handle_mixer_solo_track(params)
    elif action == "mixer.armTrack":
        return handle_mixer_arm_track(params)
    elif action == "mixer.setTrackName":
        return handle_mixer_set_track_name(params)
    elif action == "mixer.setTrackColor":
        return handle_mixer_set_track_color(params)
    elif action == "mixer.setStereoSep":
        return handle_mixer_set_stereo_sep(params)

    # Channel commands
    elif action == "channels.getCount":
        return handle_channels_get_count(params)
    elif action == "channels.getInfo":
        return handle_channels_get_info(params)
    elif action == "channels.getAll":
        return handle_channels_get_all()
    elif action == "channels.getSelected":
        return handle_channels_get_selected()
    elif action == "channels.select":
        return handle_channels_select(params)
    elif action == "channels.showInPianoRoll":
        return handle_channels_show_in_piano_roll(params)
    elif action == "channels.showEditor":
        return handle_channels_show_editor(params)
    elif action == "channels.selectOne":
        return handle_channels_select_one(params)
    elif action == "channels.triggerNote":
        return handle_channels_trigger_note(params)
    elif action == "channels.setVolume":
        return handle_channels_set_volume(params)
    elif action == "channels.setPan":
        return handle_channels_set_pan(params)
    elif action == "channels.mute":
        return handle_channels_mute(params)
    elif action == "channels.solo":
        return handle_channels_solo(params)
    elif action == "channels.setName":
        return handle_channels_set_name(params)
    elif action == "channels.setColor":
        return handle_channels_set_color(params)
    elif action == "channels.routeToMixer":
        return handle_channels_route_to_mixer(params)

    # Step sequencer commands
    elif action == "channels.getGridBit":
        return handle_channels_get_grid_bit(params)
    elif action == "channels.setGridBit":
        return handle_channels_set_grid_bit(params)
    elif action == "channels.getStepSequence":
        return handle_channels_get_step_sequence(params)
    elif action == "channels.setStepSequence":
        return handle_channels_set_step_sequence(params)
    elif action == "channels.setStepParams":
        return handle_channels_set_step_params(params)
    elif action == "channels.getStepParams":
        return handle_channels_get_step_params(params)

    # Project commands
    elif action == "project.getTempo":
        return handle_project_get_tempo()
    elif action == "project.setTempo":
        return handle_project_set_tempo(params)
    elif action == "project.getInfo":
        return handle_project_get_info()

    # Pattern commands
    elif action == "patterns.getAll":
        return handle_patterns_get_all()
    elif action == "patterns.select":
        return handle_patterns_select(params)
    elif action == "patterns.rename":
        return handle_patterns_rename(params)
    elif action == "patterns.findEmpty":
        return handle_patterns_find_empty()

    # Plugin commands
    elif action == "plugins.isValid":
        return handle_plugins_is_valid(params)
    elif action == "plugins.getName":
        return handle_plugins_get_name(params)
    elif action == "plugins.getParamCount":
        return handle_plugins_get_param_count(params)
    elif action == "plugins.getParams":
        return handle_plugins_get_params(params)
    elif action == "plugins.getParamValue":
        return handle_plugins_get_param_value(params)
    elif action == "plugins.setParamValue":
        return handle_plugins_set_param_value(params)
    elif action == "plugins.getPresetCount":
        return handle_plugins_get_preset_count(params)
    elif action == "plugins.nextPreset":
        return handle_plugins_next_preset(params)
    elif action == "plugins.prevPreset":
        return handle_plugins_prev_preset(params)
    elif action == "mixer.setEffect":
        return handle_mixer_set_effect(params)
    elif action == "mixer.getTrackEffects":
        return handle_mixer_get_track_effects(params)
    elif action == "project.undo":
        return handle_project_undo(params)
    elif action == "plugins.listPresets":
        return handle_plugins_list_presets(params)
    elif action == "browser.getFocused":
        return handle_browser_get_focused()
    elif action == "browser.navigate":
        return handle_browser_navigate(params)
    elif action == "browser.selectTab":
        return handle_browser_select_tab(params)
    elif action == "browser.toggleNode":
        return handle_browser_toggle_node(params)
    elif action == "ui.focusWindow":
        return handle_ui_focus_window(params)
    elif action == "browser.openFocused":
        return handle_browser_open_focused()
    elif action == "plugins.getColor":
        return handle_plugins_get_color(params)

    # Playlist commands
    elif action == "playlist.getState":
        return handle_playlist_get_state()
    elif action == "playlist.scrollTo":
        return handle_playlist_scroll_to(params)
    elif action == "playlist.getSelectedTrack":
        return handle_playlist_get_selected_track(params)
    elif action == "playlist.deselectTracks":
        return handle_playlist_deselect_tracks()
    elif action == "playlist.getSongPosition":
        return handle_playlist_get_song_position()
    elif action == "playlist.getTracks":
        return handle_playlist_get_tracks(params)
    elif action == "playlist.setTrackName":
        return handle_playlist_set_track_name(params)
    elif action == "playlist.setTrackMuted":
        return handle_playlist_set_track_muted(params)

    else:
        return {"error": f"Unknown action: {action}"}


# =============================================================================
# Transport Handlers
# =============================================================================


def handle_transport_start() -> dict:
    """Toggle play/pause."""
    transport.start()
    return {"is_playing": transport.isPlaying() == 1}


def handle_transport_stop() -> dict:
    """Stop playback."""
    transport.stop()
    return {"stopped": True}


def handle_transport_record() -> dict:
    """Toggle recording."""
    transport.record()
    return {"is_recording": transport.isRecording() == 1}


def handle_transport_get_status() -> dict:
    """Get transport status."""
    loop_mode = transport.getLoopMode()
    return {
        "is_playing": transport.isPlaying() == 1,
        "is_recording": transport.isRecording() == 1,
        "position": transport.getSongPosHint(),
        "loop_mode": "song" if loop_mode == 1 else "pattern",
    }


def handle_transport_set_position(params: dict) -> dict:
    """Set playback position."""
    position = params.get("position", 0)
    mode = params.get("mode", 2)  # Default: seconds
    transport.setSongPos(position, mode)
    return {"position": transport.getSongPosHint()}


def handle_transport_get_length() -> dict:
    """Get song length."""
    return {
        "bars": transport.getSongLength(midi.SONGLENGTH_BARS),
        "ticks": transport.getSongLength(midi.SONGLENGTH_ABSTICKS),
        "seconds": transport.getSongLength(midi.SONGLENGTH_S),
        "milliseconds": transport.getSongLength(midi.SONGLENGTH_MS),
    }


def handle_transport_set_loop_mode(params: dict) -> dict:
    """Set loop mode."""
    mode = params.get("mode", "pattern")
    current_mode = transport.getLoopMode()
    target_mode = 1 if mode.lower() == "song" else 0

    if current_mode != target_mode:
        transport.setLoopMode()

    return {"mode": mode}


def handle_transport_set_playback_speed(params: dict) -> dict:
    """Set playback speed."""
    speed = params.get("speed", 1.0)
    transport.setPlaybackSpeed(speed)
    return {"speed": speed}


# =============================================================================
# Mixer Handlers
# =============================================================================


def handle_mixer_get_track_count() -> dict:
    """Get number of mixer tracks."""
    return {"count": mixer.trackCount()}


def handle_mixer_get_track_info(params: dict) -> dict:
    """Get info about a mixer track."""
    track = params.get("track", 0)
    return {
        "index": track,
        "name": mixer.getTrackName(track),
        "volume": mixer.getTrackVolume(track),
        "volume_db": mixer.getTrackVolume(track, 1),
        "pan": mixer.getTrackPan(track),
        "stereo_separation": mixer.getTrackStereoSep(track),
        "is_muted": mixer.isTrackMuted(track) == 1,
        "is_solo": mixer.isTrackSolo(track) == 1,
        "is_armed": mixer.isTrackArmed(track) == 1,
        "color": hex(mixer.getTrackColor(track)),
    }


def handle_mixer_get_all_tracks(params: dict) -> dict:
    """The mixer tracks in use: named, with a channel routed to them, or holding an effect.

    `include_empty` lists every track. Each comes with the channels routed to it.
    """
    include_empty = params.get("include_empty", False)
    routed = {}
    for channel in range(channels.channelCount(True)):
        track = channels.getTargetFxTrack(channel, True)
        routed.setdefault(track, []).append(channels.getChannelName(channel, True))

    tracks = []
    for i in range(mixer.trackCount()):
        name = mixer.getTrackName(i)
        named = bool(name) and not name.startswith("Insert ")
        in_use = named or i in routed or any(
            plugins.isValid(i, slot, True) for slot in range(MIXER_SLOTS)
        )
        if not (include_empty or in_use or i == 0):
            continue
        tracks.append({
            "index": i,
            "name": name if name else ("Master" if i == 0 else f"Insert {i}"),
            "volume": mixer.getTrackVolume(i),
            "volume_db": mixer.getTrackVolume(i, 1),
            "pan": mixer.getTrackPan(i),
            "is_muted": mixer.isTrackMuted(i) == 1,
            "is_solo": mixer.isTrackSolo(i) == 1,
            "channels": routed.get(i, []),
        })

    return {"tracks": tracks}


def handle_mixer_set_track_volume(params: dict) -> dict:
    """Set mixer track volume."""
    track = params.get("track", 0)
    volume = params.get("volume", 0.8)
    mixer.setTrackVolume(track, volume)
    return {
        "volume": mixer.getTrackVolume(track),
        "volume_db": mixer.getTrackVolume(track, 1),
    }


MAX_TRACK_DB = 5.6  # a mixer fader all the way up
MIN_TRACK_DB = -80.0
FADER_SEARCH_STEPS = 24


def handle_mixer_set_track_volume_db(params: dict) -> dict:
    """Set a mixer track's fader to a level in dB.

    FL takes the fader's position (0-1, 0.8 is 0 dB) and tells the dB it gives,
    but not the other way round, so the position is found by halving.

    Seen live: the master's reading only follows a move after the command has
    returned (it answered 0 dB at every step, and the search left it at silence),
    while an insert's follows at once. All faders share one law, so the master's
    position is found on the first insert, which is put back.
    """
    track = params.get("track")
    db = params.get("db")
    error = _check_mixer_track(track)
    if error:
        return {"error": error}
    if isinstance(db, bool) or not isinstance(db, (int, float)) or not (
            MIN_TRACK_DB <= db <= MAX_TRACK_DB):
        return {"error": "db must be a number from %g to %g" % (MIN_TRACK_DB, MAX_TRACK_DB)}

    probe = track if track != 0 else 1
    before = mixer.getTrackVolume(probe)
    low, high = 0.0, 1.0
    try:
        for _ in range(FADER_SEARCH_STEPS):
            middle = (low + high) / 2
            mixer.setTrackVolume(probe, middle)
            if mixer.getTrackVolume(probe, 1) < db:
                low = middle
            else:
                high = middle
        mixer.setTrackVolume(probe, high)
        found = {
            "track": track,
            "volume": mixer.getTrackVolume(probe),
            "volume_db": mixer.getTrackVolume(probe, 1),
        }
    except Exception:
        mixer.setTrackVolume(probe, before)  # not left wherever the search was
        raise
    if probe != track:
        mixer.setTrackVolume(probe, before)
        mixer.setTrackVolume(track, high)
    return found


def handle_mixer_set_track_pan(params: dict) -> dict:
    """Set mixer track pan."""
    track = params.get("track", 0)
    pan = params.get("pan", 0.0)
    mixer.setTrackPan(track, pan)
    return {"pan": mixer.getTrackPan(track)}


def handle_mixer_mute_track(params: dict) -> dict:
    """Mute/unmute mixer track."""
    track = params.get("track", 0)
    muted = params.get("muted")  # None = toggle

    if muted is None:
        mixer.muteTrack(track, -1)
    else:
        mixer.muteTrack(track, 1 if muted else 0)

    return {
        "is_muted": mixer.isTrackMuted(track) == 1,
        "track_name": mixer.getTrackName(track),
    }


def handle_mixer_solo_track(params: dict) -> dict:
    """Solo/unsolo mixer track."""
    track = params.get("track", 0)
    solo = params.get("solo")  # None = toggle
    mode = params.get("mode", 3)

    if solo is None:
        mixer.soloTrack(track, -1, mode)
    else:
        mixer.soloTrack(track, 1 if solo else 0, mode)

    return {
        "is_solo": mixer.isTrackSolo(track) == 1,
        "track_name": mixer.getTrackName(track),
    }


def handle_mixer_arm_track(params: dict) -> dict:
    """Toggle arm state of mixer track."""
    track = params.get("track", 0)
    mixer.armTrack(track)
    return {
        "is_armed": mixer.isTrackArmed(track) == 1,
        "track_name": mixer.getTrackName(track),
    }


def handle_mixer_set_track_name(params: dict) -> dict:
    """Set mixer track name."""
    track = params.get("track", 0)
    name = params.get("name", "")
    mixer.setTrackName(track, name)
    return {"name": name}


def handle_mixer_set_track_color(params: dict) -> dict:
    """Set mixer track color."""
    track = params.get("track", 0)
    r = params.get("r", 0)
    g = params.get("g", 0)
    b = params.get("b", 0)
    # FL Studio's scripting API takes 0xRRGGBB
    color = (r << 16) | (g << 8) | b
    mixer.setTrackColor(track, color)
    return {"color": f"RGB({r}, {g}, {b})"}


def handle_mixer_set_stereo_sep(params: dict) -> dict:
    """Set mixer track stereo separation."""
    track = params.get("track", 0)
    separation = params.get("separation", 0.0)
    mixer.setTrackStereoSep(track, separation)
    return {"separation": separation}


# =============================================================================
# Channel Handlers
# =============================================================================


def handle_channels_get_count(params: dict) -> dict:
    """Get number of channels."""
    global_count = params.get("global_count", True)
    return {"count": channels.channelCount(global_count)}


def handle_channels_get_info(params: dict) -> dict:
    """Get info about a channel."""
    index = params.get("index", 0)
    use_global = params.get("use_global", True)

    return {
        "index": index,
        "name": channels.getChannelName(index, use_global),
        "color": hex(channels.getChannelColor(index, use_global)),
        "volume": channels.getChannelVolume(index, use_global),
        "pan": channels.getChannelPan(index, use_global),
        "pitch": channels.getChannelPitch(index, useGlobalIndex=use_global),
        "is_muted": channels.isChannelMuted(index, use_global) == 1,
        "is_solo": channels.isChannelSolo(index, use_global) == 1,
        "is_selected": channels.isChannelSelected(index, use_global) == 1,
        "target_fx_track": channels.getTargetFxTrack(index, use_global),
    }


def _plugin_name(channel: int):
    """The channel's plugin name, or None (no plugin, or FL couldn't say)."""
    try:
        if plugins.isValid(channel, -1, True):
            return plugins.getPluginName(channel, -1, False, True)
    except Exception:
        pass  # One odd channel shouldn't break the whole channel list.
    return None


def handle_channels_get_all() -> dict:
    """Get info about all channels."""
    channels_list = []
    count = channels.channelCount(True)

    for i in range(count):
        channels_list.append({
            "index": i,
            "name": channels.getChannelName(i, True),
            "is_muted": channels.isChannelMuted(i, True) == 1,
            "is_selected": channels.isChannelSelected(i, True) == 1,
            "target_fx_track": channels.getTargetFxTrack(i, True),
            "plugin": _plugin_name(i),
        })

    return {"channels": channels_list}


def handle_channels_get_selected() -> dict:
    """Get currently selected channel."""
    index = channels.selectedChannel(canBeNone=True, indexGlobal=True)

    if index is None or index < 0:
        return {"channel": None}

    return {
        "channel": {
            "index": index,
            "name": channels.getChannelName(index, True),
            "volume": channels.getChannelVolume(index, True),
            "pan": channels.getChannelPan(index, True),
            "is_muted": channels.isChannelMuted(index, True) == 1,
            "is_solo": channels.isChannelSolo(index, True) == 1,
        }
    }


def handle_channels_select(params: dict) -> dict:
    """Select/deselect a channel."""
    index = params.get("index", 0)
    select = params.get("select", True)
    channels.selectChannel(index, 1 if select else 0, True)
    return {
        "selected": select,
        "channel_name": channels.getChannelName(index, True),
    }


def handle_channels_show_in_piano_roll(params: dict) -> dict:
    """Make the piano roll show a channel, so piano roll scripts edit that channel.

    Selecting a channel doesn't retarget an open piano roll; hiding and showing
    the window after selecting does (verified live, FL 2026).
    """
    index = params.get("index")
    count = channels.channelCount(True)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
        return {"error": "Channel index must be 0-%d, got %r" % (count - 1, index)}
    channels.selectOneChannel(index, True)
    ui.hideWindow(midi.widPianoRoll)
    ui.showWindow(midi.widPianoRoll)
    return {"channel_name": channels.getChannelName(index, True)}


def handle_channels_show_editor(params: dict) -> dict:
    """Show or hide a channel's plugin (or channel settings) window."""
    index = params.get("index")
    count = channels.channelCount(True)
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
        return {"error": "Channel index must be 0-%d, got %r" % (count - 1, index)}
    show = bool(params.get("show", True))
    channels.showEditor(index, 1 if show else 0, True)
    return {"shown": show, "channel_name": channels.getChannelName(index, True)}


def handle_channels_select_one(params: dict) -> dict:
    """Select only one channel, deselecting others."""
    index = params.get("index", 0)
    channels.selectOneChannel(index, True)
    return {"channel_name": channels.getChannelName(index, True)}


def handle_channels_trigger_note(params: dict) -> dict:
    """Trigger a MIDI note on a channel."""
    channel = params.get("channel", 0)
    note = params.get("note", 60)
    velocity = params.get("velocity", 100)
    midi_channel = params.get("midi_channel", -1)
    channels.midiNoteOn(channel, note, velocity, midi_channel)
    return {"triggered": True, "note": note, "velocity": velocity}


def handle_channels_set_volume(params: dict) -> dict:
    """Set channel volume."""
    index = params.get("index", 0)
    volume = params.get("volume", 0.8)
    channels.setChannelVolume(index, volume, True)
    return {
        "volume": channels.getChannelVolume(index, True),
        "channel_name": channels.getChannelName(index, True),
    }


def handle_channels_set_pan(params: dict) -> dict:
    """Set channel pan."""
    index = params.get("index", 0)
    pan = params.get("pan", 0.0)
    channels.setChannelPan(index, pan, True)
    return {
        "pan": channels.getChannelPan(index, True),
        "channel_name": channels.getChannelName(index, True),
    }


def handle_channels_mute(params: dict) -> dict:
    """Mute/unmute channel."""
    index = params.get("index", 0)
    muted = params.get("muted")  # None = toggle

    if muted is None:
        channels.muteChannel(index, -1, True)
    else:
        channels.muteChannel(index, 1 if muted else 0, True)

    return {
        "is_muted": channels.isChannelMuted(index, True) == 1,
        "channel_name": channels.getChannelName(index, True),
    }


def handle_channels_solo(params: dict) -> dict:
    """Solo/unsolo channel."""
    index = params.get("index", 0)
    solo = params.get("solo")  # None = toggle

    if solo is None:
        channels.soloChannel(index, -1, True)
    else:
        channels.soloChannel(index, 1 if solo else 0, True)

    return {
        "is_solo": channels.isChannelSolo(index, True) == 1,
        "channel_name": channels.getChannelName(index, True),
    }


def handle_channels_set_name(params: dict) -> dict:
    """Set channel name."""
    index = params.get("index", 0)
    name = params.get("name", "")
    channels.setChannelName(index, name, True)
    return {"name": name}


def handle_channels_set_color(params: dict) -> dict:
    """Set channel color."""
    index = params.get("index", 0)
    r = params.get("r", 0)
    g = params.get("g", 0)
    b = params.get("b", 0)
    # FL Studio's scripting API takes 0xRRGGBB
    color = (r << 16) | (g << 8) | b
    channels.setChannelColor(index, color, True)
    return {"color": f"RGB({r}, {g}, {b})"}


def handle_channels_route_to_mixer(params: dict) -> dict:
    """Route channel to mixer track."""
    channel_index = params.get("channel_index", 0)
    mixer_track = params.get("mixer_track", 0)
    # FL silently routes to its last insert track when asked for one that isn't
    # there. trackCount() counts the master, the inserts and the "current" track.
    last_insert = mixer.trackCount() - 2
    count = channels.channelCount(True)
    if (not isinstance(channel_index, int) or isinstance(channel_index, bool)
            or not 0 <= channel_index < count):
        return {"error": "channel_index must be 0-%d, got %r" % (count - 1, channel_index)}
    if (not isinstance(mixer_track, int) or isinstance(mixer_track, bool)
            or not 0 <= mixer_track <= last_insert):
        return {"error": "mixer_track must be 0 (master) to %d, the last insert track of "
                         "this project's mixer; got %r" % (last_insert, mixer_track)}
    channels.setTargetFxTrack(channel_index, mixer_track, True)
    return {
        "channel_name": channels.getChannelName(channel_index, True),
        "mixer_track": channels.getTargetFxTrack(channel_index, True),
    }


# =============================================================================
# Step Sequencer Handlers
# =============================================================================


def handle_channels_get_grid_bit(params: dict) -> dict:
    """Get whether a step is active."""
    channel = params.get("channel", 0)
    position = params.get("position", 0)
    return {"value": channels.getGridBit(channel, position, True) == 1}


def _set_grid_bit(channel: int, position: int, on) -> None:
    """Switch a step on or off, leaving it alone if it already is.

    Seen live in FL 2026: setGridBit(1) on a step that is already on adds a
    second note to that step each time, so the state is checked first.
    """
    if (channels.getGridBit(channel, position, True) == 1) != bool(on):
        channels.setGridBit(channel, position, 1 if on else 0, True)


def handle_channels_set_grid_bit(params: dict) -> dict:
    """Set a step on or off."""
    channel = params.get("channel", 0)
    position = params.get("position", 0)
    value = params.get("value", False)
    _set_grid_bit(channel, position, value)
    return {
        "value": value,
        "channel_name": channels.getChannelName(channel, True),
    }


def handle_channels_get_step_sequence(params: dict) -> dict:
    """Get step sequence for a channel."""
    channel = params.get("channel", 0)
    steps = params.get("steps", 16)
    sequence = []

    for i in range(steps):
        sequence.append(channels.getGridBit(channel, i, True) == 1)

    return {"sequence": sequence}


def handle_channels_set_step_sequence(params: dict) -> dict:
    """Set complete step sequence for a channel."""
    channel = params.get("channel", 0)
    pattern = params.get("pattern", [])

    for i, value in enumerate(pattern):
        _set_grid_bit(channel, i, value)

    active_steps = sum(pattern)
    return {
        "active_steps": active_steps,
        "total_steps": len(pattern),
        "channel_name": channels.getChannelName(channel, True),
    }


# FL's step parameter ids (midi.pPitch ... midi.pShift), in raw FL units:
# pitch = MIDI note, velocity/release/mod/pan 0-128 (pan 64 = centre),
# fine_pitch 0-240 (120 = 0 cents), shift = ticks. The server converts from
# friendly units. FL stores shift as the step's absolute tick position
# (step * PPQ/4 + shift, seen live), so handlers convert to and from relative.
STEP_PARAMS = {
    "pitch": 0,
    "velocity": 1,
    "release": 2,
    "fine_pitch": 3,
    "pan": 4,
    "mod_x": 5,
    "mod_y": 6,
    "shift": 7,
}


def _ticks_per_step() -> int:
    """Ticks in one step sequencer step (a 16th note)."""
    return general.getRecPPQ() // 4


def handle_channels_set_step_params(params: dict) -> dict:
    """Set grid bits and step parameters; a given pattern is selected first."""
    channel = params.get("channel", 0)
    steps = params.get("steps", [])

    for step in steps:
        unknown = sorted(set(step) - set(STEP_PARAMS) - {"step", "on"})
        if unknown:
            return {"error": f"Unknown step parameter(s) {unknown}; use {sorted(STEP_PARAMS)}"}

    if params.get("pattern") is not None:
        selected = handle_patterns_select({"index": params["pattern"]})
        if "error" in selected:
            return selected
    pattern = patterns.patternNumber()
    ticks_per_step = _ticks_per_step()

    params_written = 0
    for step in steps:
        position = step["step"]
        if "on" in step:
            _set_grid_bit(channel, position, step["on"])
        for name, param_id in STEP_PARAMS.items():
            if name in step:
                value = step[name]
                if name == "shift":
                    value += position * ticks_per_step
                channels.setStepParameterByIndex(channel, pattern, position, param_id, value, True)
                params_written += 1

    return {
        "channel_name": channels.getChannelName(channel, True),
        "pattern": pattern,
        "steps_written": len(steps),
        "params_written": params_written,
    }


def handle_channels_get_step_params(params: dict) -> dict:
    """Grid bits and, for active steps, every step parameter.

    FL only reads the current pattern, so a given pattern is selected first.
    """
    channel = params.get("channel", 0)
    if params.get("pattern") is not None:
        selected = handle_patterns_select({"index": params["pattern"]})
        if "error" in selected:
            return selected
    ticks_per_step = _ticks_per_step()
    steps = []

    for position in range(params.get("steps", 16)):
        on = channels.getGridBit(channel, position, True) == 1
        step = {"step": position, "on": on}
        if on:
            for name, param_id in STEP_PARAMS.items():
                step[name] = channels.getCurrentStepParam(channel, position, param_id, True)
            step["shift"] -= position * ticks_per_step
        steps.append(step)

    return {"pattern": patterns.patternNumber(), "steps": steps}


# =============================================================================
# Project Handlers
# =============================================================================


# Tempo range accepted by FL Studio's tempo control.
MIN_TEMPO = 10.0
MAX_TEMPO = 522.0


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _current_bpm() -> float:
    # Seen live in FL 2026, opposite of the API docs: getCurrentTempo() returns
    # thousandths of a BPM (150500 = 150.5); getCurrentTempo(True) drops decimals.
    return mixer.getCurrentTempo() / 1000


def handle_project_get_tempo() -> dict:
    """Get the project tempo in BPM."""
    return {"bpm": _current_bpm()}


def handle_project_set_tempo(params: dict) -> dict:
    """Set the project tempo in BPM."""
    bpm = params.get("bpm")
    if not _is_number(bpm) or not MIN_TEMPO <= bpm <= MAX_TEMPO:
        return {"error": f"Tempo must be a number from {MIN_TEMPO} to {MAX_TEMPO} BPM, got {bpm!r}"}

    general.processRECEvent(
        midi.REC_Tempo, int(round(bpm * 1000)), midi.REC_Control | midi.REC_UpdateControl
    )
    return {"bpm": _current_bpm()}


def handle_project_get_info() -> dict:
    """Overview of the project: tempo, timebase, patterns, channels, mixer."""
    ppq = general.getRecPPQ()
    return {
        "bpm": _current_bpm(),
        "ppq": ppq,
        # getRecPPB is PPQ * beats per bar; it ignores playlist time signature markers.
        "beats_per_bar": general.getRecPPB() // ppq if ppq else None,
        "current_pattern": patterns.patternNumber(),
        "patterns": _used_patterns(),
        "channels": handle_channels_get_all()["channels"],
        "mixer_track_count": mixer.trackCount(),
        "loop_mode": "song" if transport.getLoopMode() == 1 else "pattern",
    }


# =============================================================================
# Pattern Handlers (patterns are 1-indexed)
# =============================================================================


# Step sequencer steps (16th notes) in a beat.
STEPS_PER_BEAT = 4


def _pattern_info(index: int, current: int) -> dict:
    return {
        "index": index,
        "name": patterns.getPatternName(index),
        # Seen live in FL 2026, unlike the API docs: getPatternLength counts steps.
        "length_beats": patterns.getPatternLength(index) / STEPS_PER_BEAT,
        "is_current": index == current,
    }


def _used_patterns() -> list:
    """Patterns that differ from the default empty state, in index order."""
    current = patterns.patternNumber()
    wanted = patterns.patternCount()
    found = []
    for index in range(1, patterns.patternMax() + 1):
        if len(found) >= wanted:
            break
        if not patterns.isPatternDefault(index):
            found.append(_pattern_info(index, current))
    return found


def _check_pattern_index(index):
    if isinstance(index, bool) or not isinstance(index, int):
        return f"Pattern index must be an integer, got {index!r}"
    if not 1 <= index <= patterns.patternMax():
        return f"Pattern index must be 1-{patterns.patternMax()}, got {index}"
    return None


def handle_patterns_get_all() -> dict:
    """List used patterns and the active one."""
    return {"current": patterns.patternNumber(), "patterns": _used_patterns()}


def handle_patterns_select(params: dict) -> dict:
    """Make a pattern active; an unused index creates the pattern."""
    index = params.get("index")
    error = _check_pattern_index(index)
    if error:
        return {"error": error}

    patterns.jumpToPattern(index)
    return {"index": index, "name": patterns.getPatternName(index)}


def handle_patterns_rename(params: dict) -> dict:
    """Rename a pattern (default: the active one); an empty name resets it."""
    index = params.get("index")
    if index is None:
        index = patterns.patternNumber()
    error = _check_pattern_index(index)
    if error:
        return {"error": error}

    patterns.setPatternName(index, params.get("name", ""))
    return {"index": index, "name": patterns.getPatternName(index)}


def handle_patterns_find_empty() -> dict:
    """First pattern index that is still empty (not selected)."""
    for index in range(1, patterns.patternMax() + 1):
        if patterns.isPatternDefault(index):
            return {"index": index}
    return {"error": "All patterns are in use"}


# =============================================================================
# Plugin Handlers
# =============================================================================


def handle_plugins_is_valid(params: dict) -> dict:
    """Check if plugin exists at location."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        valid = plugins.isValid(index, slot_index, True)
    else:
        valid = plugins.isValid(index, -1, use_global)

    return {"valid": valid == 1}


def handle_plugins_get_name(params: dict) -> dict:
    """Get plugin name."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        name = plugins.getPluginName(index, slot_index, False)
    else:
        name = plugins.getPluginName(index, -1, False, use_global)

    return {"name": name}


def handle_plugins_get_param_count(params: dict) -> dict:
    """Get number of plugin parameters."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        count = plugins.getParamCount(index, slot_index, True)
    else:
        count = plugins.getParamCount(index, -1, use_global)

    return {"count": count}


def handle_plugins_get_params(params: dict) -> dict:
    """A plugin's parameters: the first `max_params`, or those whose name matches `search`.

    `search` is matched word by word, ignoring case ("pitch env" finds "Pitch
    envelope - Attack time"); unnamed parameters never match.
    """
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)
    max_params = params.get("max_params", 50)
    words = str(params.get("search") or "").lower().split()
    if slot_index >= 0:
        use_global = True

    param_count = plugins.getParamCount(index, slot_index, use_global)
    param_list = []
    for i in range(param_count if words else min(param_count, max_params)):
        try:
            name = plugins.getParamName(i, index, slot_index, use_global)
            if words and not (name and all(word in name.lower() for word in words)):
                continue
            param_list.append({
                "index": i,
                "name": name,
                "value": plugins.getParamValue(i, index, slot_index, use_global),
                "value_string": plugins.getParamValueString(i, index, slot_index, use_global),
            })
        except Exception as e:
            print(f"Warning: could not read param {i}: {e}")
            continue
        if len(param_list) >= max_params:
            break

    return {"params": param_list, "total": param_count}


def handle_plugins_get_param_value(params: dict) -> dict:
    """Get specific parameter value."""
    param_index = params.get("param_index", 0)
    plugin_index = params.get("plugin_index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        name = plugins.getParamName(param_index, plugin_index, slot_index, True)
        value = plugins.getParamValue(param_index, plugin_index, slot_index, True)
        value_str = plugins.getParamValueString(param_index, plugin_index, slot_index, True)
    else:
        name = plugins.getParamName(param_index, plugin_index, -1, use_global)
        value = plugins.getParamValue(param_index, plugin_index, -1, use_global)
        value_str = plugins.getParamValueString(param_index, plugin_index, -1, use_global)

    return {
        "index": param_index,
        "name": name,
        "value": value,
        "value_string": value_str,
    }


def handle_plugins_set_param_value(params: dict) -> dict:
    """Set plugin parameter value."""
    param_index = params.get("param_index", 0)
    value = params.get("value", 0.0)
    plugin_index = params.get("plugin_index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    # setParamValue's 5th argument is pickupMode, NOT useGlobalIndex:
    # setParamValue(value, paramIndex, index, slotIndex, pickupMode, useGlobalIndex).
    # Passing True there enables pickup mode and the value is silently ignored.
    pim_none = midi.PIM_None
    if slot_index >= 0:
        name = plugins.getParamName(param_index, plugin_index, slot_index, True)
        plugins.setParamValue(value, param_index, plugin_index, slot_index, pim_none, True)
        new_value = plugins.getParamValue(param_index, plugin_index, slot_index, True)
        value_str = plugins.getParamValueString(param_index, plugin_index, slot_index, True)
    else:
        name = plugins.getParamName(param_index, plugin_index, -1, use_global)
        plugins.setParamValue(value, param_index, plugin_index, -1, pim_none, use_global)
        new_value = plugins.getParamValue(param_index, plugin_index, -1, use_global)
        value_str = plugins.getParamValueString(param_index, plugin_index, -1, use_global)

    return {
        "name": name,
        "value": new_value,
        "value_string": value_str,
    }


def handle_plugins_get_preset_count(params: dict) -> dict:
    """Get number of plugin presets."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        count = plugins.getPresetCount(index, slot_index, True)
    else:
        count = plugins.getPresetCount(index, -1, use_global)

    return {"count": count}


def handle_plugins_next_preset(params: dict) -> dict:
    """Switch to next preset."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        plugin_name = plugins.getPluginName(index, slot_index, False)
        plugins.nextPreset(index, slot_index, True)
    else:
        plugin_name = plugins.getPluginName(index, -1, False, use_global)
        plugins.nextPreset(index, -1, use_global)

    return {"plugin_name": plugin_name}


def handle_plugins_prev_preset(params: dict) -> dict:
    """Switch to previous preset."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        plugin_name = plugins.getPluginName(index, slot_index, False)
        plugins.prevPreset(index, slot_index, True)
    else:
        plugin_name = plugins.getPluginName(index, -1, False, use_global)
        plugins.prevPreset(index, -1, use_global)

    return {"plugin_name": plugin_name}


def handle_plugins_get_color(params: dict) -> dict:
    """Get plugin color."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = params.get("use_global", True)

    if slot_index >= 0:
        color = plugins.getColor(index, slot_index, True)
    else:
        color = plugins.getColor(index, -1, use_global)

    return {"color": hex(color)}


def handle_plugins_list_presets(params: dict) -> dict:
    """Names of a plugin's internal presets, in order."""
    index = params.get("index", 0)
    slot_index = params.get("slot_index", -1)
    use_global = True if slot_index >= 0 else params.get("use_global", True)

    count = plugins.getPresetCount(index, slot_index, use_global)
    names = [
        plugins.getName(index, slot_index, midi.FPN_Preset, preset, use_global)
        for preset in range(count)
    ]
    return {"count": count, "presets": names}


# =============================================================================
# Browser Handlers
# =============================================================================

BROWSER_DIRECTIONS = {"previous": midi.FPT_Up, "next": midi.FPT_Down}
MAX_BROWSER_STEPS = 500
BROWSER_FOLDER_TYPE = -100  # folders and collections are this or lower
# Two items may share a name, so the cursor only counts as stuck after a few repeats.
BROWSER_STUCK_REPEATS = 3
# Seconds one command may walk before answering, to stay inside the server's timeout.
BROWSER_TIME_BUDGET = 1.0


def _focused_browser_node() -> dict:
    return {"name": ui.getFocusedNodeCaption(), "file_type": ui.getFocusedNodeFileType()}


def handle_browser_get_focused() -> dict:
    """The item the browser's cursor is on."""
    return _focused_browser_node()


def handle_browser_navigate(params: dict) -> dict:
    """Move the browser's cursor up or down; returns every item it reached.

    `until` (a name, with `until_type` its file type) stops at the first such item.
    `collapse` closes every folder it reaches, so the walk stays on one level.
    `stuck` is True when the cursor stopped moving: the top or bottom of the list.
    It may stop before `steps` when it has taken long; `items` tells how far it got.

    FL doesn't say whether the cursor moved. The same item again means the end
    of the list, unless a different item follows: then they were neighbours alike
    in name and type (a folder holding a folder of its own name). Repeats still
    undecided at the last step are returned as `pending`: they count if the
    next command finds the cursor moving on.
    """
    direction = BROWSER_DIRECTIONS.get(params.get("direction", "next"))
    steps = params.get("steps", 1)
    until = params.get("until")
    until_type = params.get("until_type")
    collapse = params.get("collapse", False)
    if direction is None:
        return {"error": "direction must be 'next' or 'previous'"}
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= MAX_BROWSER_STEPS:
        return {"error": "steps must be 1-%d" % MAX_BROWSER_STEPS}

    reached = []
    repeats = 0
    previous = _focused_browser_node()
    started = time.time()
    for _ in range(steps):
        ui.navigateBrowser(direction, False)
        node = _focused_browser_node()
        if collapse and node["file_type"] <= BROWSER_FOLDER_TYPE:
            ui.toggleBrowserNode(0)
        if node == previous:
            repeats += 1
            if repeats >= BROWSER_STUCK_REPEATS:
                return {"items": reached, "found": False, "stuck": True, "pending": []}
            continue
        reached.extend([previous] * repeats)
        repeats = 0
        previous = node
        reached.append(node)
        if node["name"] == until and until_type in (None, node["file_type"]):
            return {"items": reached, "found": True, "stuck": False, "pending": []}
        if time.time() - started > BROWSER_TIME_BUDGET:
            break
    return {"items": reached, "found": False, "stuck": False, "pending": [previous] * repeats}


def handle_browser_select_tab(params: dict) -> dict:
    """Step through the browser's tabs; returns the tab now showing."""
    directions = {"left": midi.FPT_Left, "right": midi.FPT_Right, "first": 0}
    direction = directions.get(params.get("direction", "first"))
    if direction is None:
        return {"error": "direction must be 'left', 'right' or 'first'"}
    return {"tab": ui.navigateBrowserTabs(direction)}


def handle_browser_toggle_node(params: dict) -> dict:
    """Expand (1), collapse (0) or toggle (-1) the focused folder."""
    ui.toggleBrowserNode(params.get("value", -1))
    return _focused_browser_node()


def handle_browser_open_focused() -> dict:
    """Open the focused item, as clicking it would."""
    node = _focused_browser_node()
    ui.selectBrowserMenuItem()
    return node


# =============================================================================
# UI Handlers
# =============================================================================

UI_WINDOWS = {
    "mixer": midi.widMixer, "channel rack": midi.widChannelRack,
    "playlist": midi.widPlaylist, "piano roll": midi.widPianoRoll,
    "browser": midi.widBrowser,
}


def _ui_state() -> dict:
    return {
        "focused_window": ui.getFocusedFormCaption(),
        "focused_window_id": ui.getFocusedFormID(),
        "in_popup_menu": bool(ui.isInPopupMenu()),
    }


def handle_ui_focus_window(params: dict) -> dict:
    """Show one of FL's main windows and give it the focus."""
    window = UI_WINDOWS.get(params.get("window"))
    if window is None:
        return {"error": "window must be one of: %s" % ", ".join(sorted(UI_WINDOWS))}
    ui.showWindow(window)
    ui.setFocused(window)
    return _ui_state()


# =============================================================================
# Mixer effect slots and undo
# =============================================================================

MIXER_SLOTS = 10
# A slot's mix knob at 100%. Seen live: it is set on one scale and read back on another.
EFFECT_MIX_FULL = 12800
EFFECT_MIX_READ_FULL = 1 << 30
MAX_UNDO_STEPS = 50


def _check_mixer_track(track) -> str:
    if not isinstance(track, int) or isinstance(track, bool):
        return "track must be an integer"
    if not 0 <= track < mixer.trackCount():
        return "track must be 0-%d" % (mixer.trackCount() - 1)
    return ""


def _effect_event(track: int, slot: int, event: int) -> int:
    """ID of one of an effect slot's own controls (mute switch, mix level)."""
    return mixer.getTrackPluginId(track, slot) + event


def _track_effects(track: int) -> list:
    return [
        {
            "slot": slot,
            "plugin": plugins.getPluginName(track, slot, False, True),
            "enabled": mixer.getEventValue(_effect_event(track, slot, midi.REC_Plug_Mute)) != 0,
            "mix": round(
                mixer.getEventValue(_effect_event(track, slot, midi.REC_Plug_MixLevel))
                / EFFECT_MIX_READ_FULL, 3),
        }
        for slot in range(MIXER_SLOTS)
        if plugins.isValid(track, slot, True)
    ]


def handle_mixer_set_effect(params: dict) -> dict:
    """Switch an effect slot on or off and/or set how much of it is mixed in (0-1)."""
    track = params.get("track")
    slot = params.get("slot")
    enabled = params.get("enabled")
    mix = params.get("mix")
    error = _check_mixer_track(track)
    if error:
        return {"error": error}
    if not isinstance(slot, int) or isinstance(slot, bool) or not 0 <= slot < MIXER_SLOTS:
        return {"error": "slot must be 0-%d" % (MIXER_SLOTS - 1)}
    if not plugins.isValid(track, slot, True):
        return {"error": "Mixer track %d has no effect in slot %d" % (track, slot)}
    if mix is not None and (isinstance(mix, bool) or not isinstance(mix, (int, float))
                            or not 0 <= mix <= 1):
        return {"error": "mix must be a number from 0 to 1"}
    if enabled is not None and not isinstance(enabled, bool):
        return {"error": "enabled must be true or false"}
    if enabled is None and mix is None:
        return {"error": "Give enabled, mix or both"}

    flags = midi.REC_Control | midi.REC_UpdateControl
    if enabled is not None:
        event = _effect_event(track, slot, midi.REC_Plug_Mute)
        general.processRECEvent(event, 1 if enabled else 0, flags)
    if mix is not None:
        event = _effect_event(track, slot, midi.REC_Plug_MixLevel)
        general.processRECEvent(event, int(round(mix * EFFECT_MIX_FULL)), flags)
    return {"track": track, "effects": _track_effects(track)}


def handle_mixer_get_track_effects(params: dict) -> dict:
    """The effect plugins in a mixer track's slots."""
    track = params.get("track")
    error = _check_mixer_track(track)
    if error:
        return {"error": error}
    return {"track": track, "name": mixer.getTrackName(track), "effects": _track_effects(track)}


def handle_project_undo(params: dict) -> dict:
    """Step back in FL's undo history."""
    steps = params.get("steps", 1)
    if not isinstance(steps, int) or isinstance(steps, bool) or not 1 <= steps <= MAX_UNDO_STEPS:
        return {"error": "steps must be 1-%d" % MAX_UNDO_STEPS}
    for _ in range(steps):
        general.undoUp()
    return {
        "undone": steps,
        "history_position": general.getUndoHistoryPos(),
        "history_count": general.getUndoHistoryCount(),
    }


# =============================================================================
# Playlist
# =============================================================================

# The API can't place or read clips. It can scroll the playlist, name its
# tracks, tell which track is selected and how long the song is; the server
# places clips by posting clicks to the playlist's window and checks with these.


def _whole(value, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def handle_playlist_get_state() -> dict:
    """Whether the playlist shows, and the song's length (0 ticks: no clips)."""
    ticks = transport.getSongLength(midi.SONGLENGTH_ABSTICKS)
    bar_ticks = general.getRecPPB()
    return {
        "visible": bool(ui.getVisible(midi.widPlaylist)),
        # Seen live: one tick less than where the last clip ends.
        "length_ticks": ticks + 1 if ticks > 0 else 0,
        "length_bars": (ticks + 1) / bar_ticks if ticks > 0 else 0,
        "bar_ticks": bar_ticks,
        "track_count": playlist.trackCount(),
    }


def handle_playlist_scroll_to(params: dict) -> dict:
    """Scroll the playlist: `bar` becomes its first bar, `track` comes into view."""
    bar, track = params.get("bar"), params.get("track")
    if bar is not None:
        if not _whole(bar, 1, 100000):
            return {"error": "bar must be a whole number of 1 or more, got %r" % (bar,)}
        ui.scrollWindow(midi.widPlaylist, bar, 1)
    if track is not None:
        if not _whole(track, 1, playlist.trackCount()):
            return {"error": "track must be 1-%d, got %r" % (playlist.trackCount(), track)}
        ui.scrollWindow(midi.widPlaylist, track, 0)
    return {"bar": bar, "track": track}


def handle_playlist_get_selected_track(params: dict) -> dict:
    """The selected playlist tracks (1-based), looking at tracks 1 to `upto`."""
    count = playlist.trackCount()
    upto = params.get("upto", count)
    if not _whole(upto, 1, 100000):
        return {"error": "upto must be a whole number of 1 or more, got %r" % (upto,)}
    return {"selected": [i for i in range(1, min(upto, count) + 1) if playlist.isTrackSelected(i)]}


def handle_playlist_deselect_tracks() -> dict:
    """Deselect every playlist track, so the next header click shows what it selected."""
    playlist.deselectAll()
    return {"selected": []}


def handle_playlist_get_song_position() -> dict:
    """The song position in ticks. It never lies beyond the end of the last clip."""
    return {"ticks": transport.getSongPos(midi.SONGLENGTH_ABSTICKS)}


def handle_playlist_get_tracks(params: dict) -> dict:
    """Names and mute state of the first `count` playlist tracks."""
    count = params.get("count", 20)
    if not _whole(count, 1, playlist.trackCount()):
        return {"error": "count must be 1-%d, got %r" % (playlist.trackCount(), count)}
    return {"tracks": [
        {"track": i, "name": playlist.getTrackName(i), "is_muted": bool(playlist.isTrackMuted(i))}
        for i in range(1, count + 1)
    ]}


def handle_playlist_set_track_name(params: dict) -> dict:
    """Name a playlist track; an empty name resets it."""
    track = params.get("track")
    if not _whole(track, 1, playlist.trackCount()):
        return {"error": "track must be 1-%d, got %r" % (playlist.trackCount(), track)}
    playlist.setTrackName(track, str(params.get("name", "")))
    return {"track": track, "name": playlist.getTrackName(track)}


def handle_playlist_set_track_muted(params: dict) -> dict:
    """Mute or unmute a playlist track (its mute stays with the track number, not its clips)."""
    track = params.get("track")
    muted = params.get("muted")
    if not _whole(track, 1, playlist.trackCount()):
        return {"error": "track must be 1-%d, got %r" % (playlist.trackCount(), track)}
    if not isinstance(muted, bool):
        return {"error": "muted must be true or false, got %r" % (muted,)}
    playlist.muteTrack(track, 1 if muted else 0)
    return {"track": track, "is_muted": bool(playlist.isTrackMuted(track))}
