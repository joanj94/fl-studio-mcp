"""FL Studio MCP Server - Control FL Studio via Model Context Protocol.

This MCP server provides tools to control FL Studio through two mechanisms:
1. MIDI + JSON - Real-time control of mixer, transport, channels, and plugins
2. Piano Roll Scripts - Persistent note placement via JSON + keystroke triggering

Requirements:
- FL Studio 20.7+ running
- FLStudioMCP controller script installed (run: ./scripts/setup_mac.sh)
- On Mac: IAC Driver enabled in Audio MIDI Setup
- On Windows: loopMIDI virtual MIDI ports configured
- For piano roll: ComposeWithLLM.pyscript installed in FL Studio

Limitations:
- Cannot load new plugins (only control existing ones)
- Cannot place pattern clips in the playlist or create automation
"""

from __future__ import annotations

from fastmcp import FastMCP

from fl_studio_mcp.tools import (
    register_arrange_tools,
    register_audio_tools,
    register_channel_setup_tools,
    register_channel_tools,
    register_mixer_tools,
    register_music_tools,
    register_pattern_tools,
    register_piano_roll_tools,
    register_plugin_tools,
    register_project_tools,
    register_roles_tools,
    register_style_tools,
    register_transport_tools,
)
from fl_studio_mcp.utils.connection import get_connection, reset_connection

# Create the MCP server
mcp = FastMCP(
    name="fl-studio-mcp",
    instructions="""
FL Studio MCP Server - Control FL Studio from AI assistants.

This server provides tools to control FL Studio through its Python scripting API.
FL Studio must be running with the "FL Studio MCP Controller" MIDI script enabled.
Start with fl_get_project_overview to see tempo, patterns and channels.
Channels can be addressed by role: a word from the channel's name ("kick" finds
"808 Kick"). fl_get_roles lists them; fl_check_roles checks a project has the
parts you need. Note and step tools accept a role wherever they take a channel.

Available tool categories:
- Project: Overview, tempo (fl_get_project_overview, fl_set_tempo)
- Patterns: List, select (an unused index creates the pattern), rename, find empty
- Roles: fl_get_roles, fl_check_roles
- Styles (style_*): optional style packs with tempo range, scales, roles, song
  structure, rhythm grids, progressions and guidance. style_list, style_get,
  style_check_project. Treat them as starting points; the user's ideas win.
- Transport: Play, stop, record, position control
- Mixer: Volume, pan, mute, solo, track management
- Channels: Channel info, note triggering, step sequencer incl. per-step pitch,
  velocity and pan (fl_set_step_params)
- Channel setup: fl_add_channel adds a channel with an instrument plugin from
  fl_list_generators. It types into FL's plugin picker, so FL comes to the front.
- Plugins: Parameter control, preset navigation
- Piano roll: Write, delete and read persistent notes (fl_send_notes, ...). Pass
  channel/pattern to aim at a roll; notes may carry pan, slide, porta, fine_pitch
  (cents) and more.
- Arrangement and feedback: fl_arrange builds a song from patterns (sections of
  looped patterns written into one long pattern; play it in pattern mode).
  fl_render renders what FL would play to a WAV and returns measurements
  (loudness, peaks, frequency balance, stereo image, key, loudness over time);
  audio_analyze measures any WAV. You can't hear the result, so use them to
  check your work: render, read the numbers, adjust levels or notes, render again.
- Music (music_*): Build notes from scales, chords, roman numerals, scale degrees,
  rhythm grids and rolls, and transform them (arpeggiate, transpose, humanize, ...).
  These don't touch FL Studio; their output goes straight into fl_send_notes.
  Call music_reference once to see the vocabulary. Pitch names use C4 = MIDI 60.

Important limitations:
1. Cannot load effects, samples or presets - fl_add_channel only adds an
   instrument plugin in its default state
2. Cannot place pattern clips in the playlist or create automation. To build a
   song, fl_arrange writes sections of looped patterns into one long pattern.
3. Note triggering (fl_trigger_note) is real-time only - notes won't persist
   unless FL Studio is recording. Use step sequencer (fl_set_grid_bit) for
   persistent drum patterns.
""",
)


# Register connection status resource
@mcp.resource("fl://status")
def get_fl_status() -> str:
    """Get FL Studio connection status."""
    conn = get_connection()
    if conn.is_connected:
        return "Connected to FL Studio via MIDI"
    else:
        return f"Not connected: {conn.connection_error}"


@mcp.resource("fl://project")
def get_project_info() -> dict:
    """Get current FL Studio project information."""
    conn = get_connection()

    if not conn.is_connected:
        return {"error": conn.connection_error}

    try:
        result = conn.send_command("transport.getStatus")
        if not result.get("success", False) and "error" in result:
            return {"error": result["error"]}

        return {
            "is_playing": result.get("is_playing", False),
            "is_recording": result.get("is_recording", False),
            "position": result.get("position", ""),
            "loop_mode": result.get("loop_mode", "pattern"),
        }
    except Exception as e:
        return {"error": str(e)}


# Connection management tools
@mcp.tool()
def fl_connect() -> str:
    """Connect or reconnect to FL Studio via MIDI.

    Use this tool to:
    - Check if FL Studio is connected
    - Retry connection after starting FL Studio
    - Reconnect if the connection was lost

    Returns the connection status.
    """
    # Reset connection state to force a fresh connection attempt
    reset_connection()

    conn = get_connection()
    try:
        conn.ensure_connected()
    except RuntimeError as e:
        return f"Connection failed: {e}"
    return "Successfully connected to FL Studio via MIDI!"


@mcp.tool()
def fl_connection_status() -> dict:
    """Get the current FL Studio connection status.

    Returns information about whether FL Studio is connected
    and any error messages if not.
    """
    conn = get_connection()
    # Eagerly attempt a connection so the reported status reflects actual
    # reachability rather than the lazily-initialized flag.
    try:
        conn.ensure_connected()
    except RuntimeError:
        pass
    status = conn.get_status()
    return {
        "connected": status.get("connected", False),
        "port_name": status.get("port_name"),
        "available_ports": status.get("available_ports", []),
        "error": status.get("error"),
    }


# Register all tools
register_transport_tools(mcp)
register_mixer_tools(mcp)
register_channel_tools(mcp)
register_channel_setup_tools(mcp)
register_plugin_tools(mcp)
register_piano_roll_tools(mcp)
register_arrange_tools(mcp)
register_audio_tools(mcp)
register_music_tools(mcp)
register_project_tools(mcp)
register_pattern_tools(mcp)
register_roles_tools(mcp)
register_style_tools(mcp)


def main():
    """Run the FL Studio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
