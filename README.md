# FL Studio MCP Server

An MCP (Model Context Protocol) server that lets AI assistants control FL Studio (transport, mixer, channel rack, plugins and piano roll) through MIDI communication and Piano Roll scripts.

This is a fork of [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp). The goal is a **genre-agnostic toolkit** that any MCP-compatible AI can use to write any melody or style in FL Studio. The server provides precise, musically-expressed building blocks, and the AI makes the musical decisions. Hardcore/hardstyle is the first style used to test it; no genre logic lives in the server code.

> **Status:** early development. The foundation (reliable communication, tests) is done; the music toolkit and extra FL control are in progress. See [Roadmap](#roadmap).

## Features

### Transport Control

- Play, pause, stop playback
- Toggle recording
- Set playback position
- Get song length and position
- Control loop mode (pattern/song)
- Adjust playback speed

### Mixer Control

- Get/set track volume and pan
- Mute/solo tracks
- Arm tracks for recording
- Set track names and colors
- Stereo separation control

### Channel Rack Control

- List all channels
- Get/set channel properties (volume, pan, name, color)
- Mute/solo channels
- Route channels to mixer tracks
- Trigger MIDI notes in real-time
- Step sequencer control (get/set grid bits)

### Plugin Control

- List plugin parameters
- Get/set parameter values
- Navigate presets (next/previous)
- Query plugin info

### Piano Roll Control

- **Add notes** to the piano roll with precise timing
- **Add chords** with a single command
- **Delete specific notes** by MIDI number and time
- **Clear all notes** from the piano roll
- **Read piano roll state** to see all existing notes
- Auto-triggering via keystroke (Cmd+Opt+Y on macOS, Ctrl+Alt+Y on Windows), with confirmation from FL Studio of what was applied

### Music Toolkit

Genre-agnostic helpers that build and transform notes **without touching FL Studio**. Their output is in the same format `fl_send_notes` accepts, so an AI can generate, combine and check material before writing it:

- Scales and modes, chord symbols (`Fm7`, `C/E`) and roman numerals (`i-VI-III-VII`, `V7`, `bVII`)
- Melodies as scale degrees (`[1, 3, 5, None, 8]`)
- Step-grid rhythms (`X...x...|x_..x.x.`) and Euclidean rhythms (`E(3,8)`)
- Rolls that speed up (for fills and build-ups)
- Transforms: arpeggiate, transpose, shift, stretch, reverse, quantize, swing, humanize, snap to scale, legato, repeat, velocity ramp

Pitch names use scientific notation, where **C4 = MIDI 60**. FL Studio's piano roll labels the same note C5.

## Changes in this fork

Compared with upstream, this fork currently adds:

- **Reliable command/response protocol:** every command carries a unique id that FL echoes back, so a late or stale response is never mistaken for the current one. The server writes its JSON files atomically (retried if FL has the file open), and skips half-written responses from FL.
- **Correct error reporting:** unknown actions and FL-side errors are now reported as failures; before, they came back as `success: true`.
- **Piano roll confirmation:** piano roll tools wait for FL Studio to confirm which requests it processed and report notes added/deleted (or FL's error message), instead of sleeping a fixed 2 seconds and assuming success. A failing batch is dropped rather than replayed on every later trigger.
- **Safe MIDI port selection:** the server never falls back to an arbitrary MIDI port (which could be a hardware synth); set `FL_MCP_MIDI_PORT` to choose one explicitly.
- **OneDrive-aware paths:** the real Windows Documents folder is used everywhere (server, FL-side scripts, installer), so redirected Documents folders work.
- **Test suite:** pytest with the FL Studio API faked, so it runs without FL Studio. It includes a contract test that checks every tool against the FL-side controller script.

## Roadmap

1. **Foundation**: reliable protocol, tests, port and path handling. ✅
2. **Generic music toolkit** (pure Python): notes in musical units, scales and modes, chord symbols and roman numerals, scale-degree melodies, rhythm grids and Euclidean rhythms, rolls, transforms (arpeggiate, transpose, quantize, swing, humanize, ...). ✅
3. **More FL control**: tempo and time signature, pattern selection, writing notes into a specific channel and pattern, and full note properties (slide, porta, pan, fine pitch).
4. **Roles and templates**: refer to instruments by role (`"kick"`, `"lead"`, …) instead of channel numbers, based on a template project you prepare.
5. **Style packs**: optional data and prompt files describing a genre (tempo range, structure, idioms); hardstyle and hardcore first, plus one very different genre to keep the core generic.
6. **Render and analysis** (optional): render via FL's command line and give the AI feedback on loudness, spectrum and key.

## Important Limitations

These come from FL Studio's scripting API, not from this server:

- **Cannot load plugins.** You can only control parameters of plugins already loaded in your project, so start from a template project that contains the instruments you want.
- **Cannot create patterns or place clips in the playlist.** Work within existing patterns.
- **Real-time notes aren't saved.** `fl_trigger_note` plays a note live; it only ends up in the project if FL Studio is recording. Use the piano roll or step sequencer tools to write notes permanently.

## Requirements

- **FL Studio 20.7+** (MIDI Controller Scripting API) and a version with Python piano roll scripting (**Tools > Scripting**) for the piano roll tools
- **Python 3.10+** (the Windows installer uses Python 3.12, which has prebuilt `python-rtmidi` wheels)
- **Windows** or **macOS**
  - Windows: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)
  - macOS: IAC Driver (built-in, needs to be enabled)

## Which AI Clients Work With This?

This is a standard [MCP](https://modelcontextprotocol.io) server that talks to clients over stdio. It isn't tied to any AI vendor, so **any MCP-compatible client can connect**: Claude Desktop, Claude Code, Cursor, Windsurf, Gemini CLI, OpenAI Codex CLI, and others.

The installers auto-configure only **Claude Desktop** and **Claude Code** (`scripts/install_mcp_for_claude.ps1` / `.sh`). For any other client, add an equivalent server entry to its MCP config:

```json
{
  "command": "uv",
  "args": ["run", "--directory", "/path/to/fl-studio-mcp", "fl-studio-mcp"]
}
```

Adjust `/path/to/fl-studio-mcp` to your local clone.

## Quick Installation

### Windows

```powershell
# Clone the repository
git clone https://github.com/joanj94/fl-studio-mcp.git
cd fl-studio-mcp

# Allow running local scripts for this user (one-time)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force

# Run the one-command installer
.\install.ps1
```

### macOS

```bash
# Clone the repository
git clone https://github.com/joanj94/fl-studio-mcp.git
cd fl-studio-mcp

# Run the one-command installer
./install.sh
```

Both installers will:

1. Install [uv](https://github.com/astral-sh/uv) if not present
2. Install Python dependencies
3. Guide you through enabling virtual MIDI ports (loopMIDI on Windows, IAC Driver on macOS)
4. Install the FL Studio MIDI controller script
5. Install the Piano Roll script (ComposeWithLLM)
6. Configure Claude Desktop or Claude Code

> **Windows piano-roll note:** the Piano Roll script is launched by sending FL Studio a keystroke (`Ctrl+Alt+Y`). This needs the FL Studio window in the foreground, so **FL Studio will pop to the front for a moment** each time a piano roll tool runs. This is expected.

## Manual Installation

### 1. Install Python Dependencies

```bash
# Using uv (recommended; on Windows add --python 3.12)
uv sync

# Or using pip
pip install -e .
```

### 2. Enable Virtual MIDI Ports

#### Windows (loopMIDI)

1. Install [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html), from the website or with `winget install TobiasErichsen.loopMIDI`
2. Open loopMIDI and click **+** to create a port. The default name `loopMIDI Port` works; the server finds any port with `loopMIDI` in its name
3. Keep loopMIDI running while using FL Studio (it can start with Windows from its tray icon)

#### macOS (IAC Driver)

1. Open **Audio MIDI Setup** (search in Spotlight)
2. Press **Cmd+2** or go to **Window > Show MIDI Studio**
3. Double-click on **IAC Driver**
4. Check **"Device is online"**
5. Click **Apply**

### 3. Install FL Studio Scripts

Copy two files into FL Studio's Settings folder, which is `<Documents>\Image-Line\FL Studio\Settings`. On Windows, "Documents" may be redirected to OneDrive; the installer handles this automatically.

| File | Destination inside `Settings` |
|------|-------------------------------|
| `fl_controller/device_FLStudioMCP.py` | `Hardware/FLStudioMCP/` |
| `scripts/ComposeWithLLM.pyscript` | `Piano roll scripts/` |

```powershell
# Windows (PowerShell)
$settings = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Image-Line\FL Studio\Settings"
New-Item -ItemType Directory -Force "$settings\Hardware\FLStudioMCP" | Out-Null
Copy-Item fl_controller\device_FLStudioMCP.py "$settings\Hardware\FLStudioMCP\"
Copy-Item scripts\ComposeWithLLM.pyscript "$settings\Piano roll scripts\"
```

```bash
# macOS
SETTINGS=~/Documents/Image-Line/FL\ Studio/Settings
mkdir -p "$SETTINGS/Hardware/FLStudioMCP"
cp fl_controller/device_FLStudioMCP.py "$SETTINGS/Hardware/FLStudioMCP/"
cp scripts/ComposeWithLLM.pyscript "$SETTINGS/Piano roll scripts/"
```

After pulling changes to either script, copy it again and restart FL Studio (or reload the script); FL runs the installed copy, not the one in this repo.

### 4. Configure FL Studio

1. **Restart FL Studio** (if it's running) so it finds the new controller script
2. Go to **Options > MIDI Settings**
3. Under **Input**, select your virtual MIDI port (e.g. "loopMIDI Port" or "IAC Driver Bus 1")
4. Set **Controller type** to **FL Studio MCP Controller** (listed under *Scripts*)
5. Make sure **Enable** is lit for that port

The port only needs to be enabled under **Input**. Leave it alone under **Output**: the server sends to FL Studio, never the other way.

For the piano roll tools, also open a pattern's piano roll and run **Tools > Scripting > ComposeWithLLM** from the piano roll window's own menu once per FL session (see [Piano Roll Workflow](#piano-roll-workflow)).

### 5. Configure Your MCP Client

For Claude Desktop, add this to its config (Windows: `%APPDATA%\Claude\claude_desktop_config.json`, macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "fl-studio": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/fl-studio-mcp", "fl-studio-mcp"]
    }
  }
}
```

For Claude Code, run `claude mcp add`. For other clients, see [Which AI Clients Work With This?](#which-ai-clients-work-with-this).

### Optional environment variables

Set these in the MCP server entry's `env` block if you need them:

| Variable | Purpose |
|----------|---------|
| `FL_MCP_MIDI_PORT` | MIDI output port to use (exact name or case-insensitive substring). Without it, the server picks the first port whose name contains `loopMIDI`, `IAC` or `FL`, and refuses to guess otherwise. |
| `FL_MCP_SETTINGS_DIR` | Full path to FL Studio's `Settings` folder, if you changed FL's user data folder. The default is `<Documents>\Image-Line\FL Studio\Settings`. |

## Usage

### Running the Server Manually

```bash
uv run fl-studio-mcp
```

### Piano Roll Workflow

1. Open FL Studio and select a channel
2. Open the Piano Roll (F7 or double-click the channel)
3. The first time in each FL session, run the script manually from the **piano roll's own menu** (the ▸ arrow in the piano roll window's top-left corner): **Tools > Scripting > ComposeWithLLM**. FL's main Tools menu in the top bar is a different menu and doesn't list piano roll scripts
4. After that, the MCP tools trigger the script automatically and report what FL Studio applied

## Available Tools

### Connection

| Tool | Description |
|------|-------------|
| `fl_connect` | Connect/reconnect to FL Studio |
| `fl_connection_status` | Get connection status and available MIDI ports |

### Transport

| Tool | Description |
|------|-------------|
| `fl_play` | Start/pause playback |
| `fl_stop` | Stop playback |
| `fl_record` | Toggle recording |
| `fl_get_transport_status` | Get playback/recording state |
| `fl_set_song_position` | Set playback position |
| `fl_get_song_length` | Get song duration |
| `fl_set_loop_mode` | Switch between pattern/song mode |
| `fl_set_playback_speed` | Adjust playback speed (0.25x-4x) |

### Mixer

| Tool | Description |
|------|-------------|
| `fl_get_mixer_track_count` | Get number of mixer tracks |
| `fl_get_mixer_track_info` | Get track details |
| `fl_get_all_mixer_tracks` | List all tracks |
| `fl_set_track_volume` | Set track volume |
| `fl_set_track_pan` | Set track pan |
| `fl_mute_track` | Mute/unmute track |
| `fl_solo_track` | Solo/unsolo track |
| `fl_arm_track` | Arm track for recording |
| `fl_set_track_name` | Rename track |
| `fl_set_track_color` | Set track color |
| `fl_set_stereo_separation` | Adjust stereo width |

### Channels

| Tool | Description |
|------|-------------|
| `fl_get_channel_count` | Get number of channels |
| `fl_get_channel_info` | Get channel details |
| `fl_get_all_channels` | List all channels |
| `fl_get_selected_channel` | Get selected channel |
| `fl_select_channel` | Select/deselect channel |
| `fl_select_one_channel` | Select channel exclusively |
| `fl_trigger_note` | Trigger MIDI note (real-time) |
| `fl_set_channel_volume` | Set channel volume |
| `fl_set_channel_pan` | Set channel pan |
| `fl_mute_channel` | Mute/unmute channel |
| `fl_solo_channel` | Solo/unsolo channel |
| `fl_set_channel_name` | Rename channel |
| `fl_set_channel_color` | Set channel color |
| `fl_route_channel_to_mixer` | Route to mixer track |
| `fl_get_grid_bit` | Get step sequencer step |
| `fl_set_grid_bit` | Set step sequencer step |
| `fl_get_step_sequence` | Get full pattern |
| `fl_set_step_sequence` | Set full pattern |

### Plugins

| Tool | Description |
|------|-------------|
| `fl_is_plugin_valid` | Check if plugin exists |
| `fl_get_plugin_name` | Get plugin name |
| `fl_get_plugin_param_count` | Get parameter count |
| `fl_get_plugin_params` | List all parameters |
| `fl_get_plugin_param_value` | Get parameter value |
| `fl_set_plugin_param_value` | Set parameter value |
| `fl_get_preset_count` | Get preset count |
| `fl_next_preset` | Next preset |
| `fl_prev_preset` | Previous preset |
| `fl_get_plugin_color` | Get plugin color |

### Piano Roll

| Tool | Description |
|------|-------------|
| `fl_send_notes` | Add notes to the piano roll |
| `fl_send_chord` | Add a chord (multiple notes at same time) |
| `fl_delete_notes` | Delete specific notes |
| `fl_clear_piano_roll` | Clear all notes |
| `fl_get_piano_roll_state` | Read current piano roll notes |
| `fl_trigger_script` | Manually trigger the FL Studio script for queued requests |
| `fl_get_piano_roll_info` | Get piano roll system info |
| `fl_clear_request_queue` | Cancel pending queued changes |

### Music (no FL Studio needed)

| Tool | Description |
|------|-------------|
| `music_reference` | List scales, chord qualities, transform operations and grid syntax |
| `music_scale` | Pitches of a scale |
| `music_chords` | Chord progression from symbols and/or roman numerals |
| `music_degrees` | Melody from scale degrees |
| `music_rhythm` | Notes from a step-grid or Euclidean pattern |
| `music_roll` | Roll that speeds up, with a velocity ramp |
| `music_transform` | Apply a list of transform operations to notes |

## Example Prompts

```text
"Set the volume of mixer track 1 to 80% and pan it slightly left"
"Put kicks on steps 0, 4, 8 and 12 of channel 0"
"Add a C major arpeggio starting at beat 0: C4, E4, G4, C5, each a quarter note"
"Add Am at beat 0, F at beat 4, C at beat 8 and G at beat 12, each 4 beats long"
"List the parameters of the plugin on channel 0 and set the filter cutoff to 50%"
"Write an F minor i-VI-III-VII progression, arpeggiate it in 16ths up and down over two octaves, and put it in the piano roll"
"Add a snare roll over the last 2 bars that goes 8ths, 16ths, 32nds and gets louder"
```

## Troubleshooting

### "Not connected to FL Studio" / "No virtual MIDI port found"

1. Make sure loopMIDI is running (Windows) or the IAC Driver is enabled (macOS)
2. Run `fl_connection_status` to see which MIDI ports the server can see
3. The server only auto-selects ports named like `loopMIDI`, `IAC` or `FL`. If yours is named differently, set `FL_MCP_MIDI_PORT`
4. Check that the **FL Studio MCP Controller** is set on that port under **Input** in FL's MIDI Settings, and restart FL Studio after enabling it

### "Timeout waiting for FL Studio response"

1. Check that FL Studio is running and the controller is enabled on the same port the server uses
2. Open FL's script output window (**View > Script output**) and look for errors from the FLStudioMCP controller
3. Make sure the installed controller script is up to date: copy `fl_controller/device_FLStudioMCP.py` again after updating this repo. An old copy doesn't echo request ids, so every command times out

### Piano roll: "FL Studio did not respond"

1. A piano roll must be open in FL Studio
2. Run **Tools > Scripting > ComposeWithLLM** manually once per FL session, from the piano roll window's menu (not FL's main Tools menu)
3. Press `Ctrl+Alt+Y` (Windows) or `Cmd+Opt+Y` (macOS) yourself to check the hotkey runs the script; the requests stay queued until then
4. Make sure the installed `ComposeWithLLM.pyscript` is up to date (same reason as above)
5. On Windows, FL Studio must not be minimized to the system tray; on macOS, grant Accessibility permissions when prompted
6. After updating the server code, **fully restart** your MCP client. Reconnecting the MCP server alone may keep the old process running

## Architecture

```text
┌─────────────────┐     ┌─────────────────────────────────────────┐
│   MCP Client    │────▶│           FastMCP Server                │
│  (Claude, etc)  │     │                                         │
└─────────────────┘     │  ┌─────────────────┐  ┌──────────────┐  │
                        │  │ MIDI + JSON     │  │ Piano Roll   │  │
                        │  │ Tools           │  │ Tools (JSON) │  │
                        │  └────────┬────────┘  └──────┬───────┘  │
                        └───────────┼──────────────────┼──────────┘
                                    │                  │
                               MIDI + JSON        JSON Files +
                                    │              Keystroke
                                    ▼                  ▼
                        ┌─────────────────────────────────────────┐
                        │              FL Studio                  │
                        │  ┌──────────────┐  ┌──────────────────┐ │
                        │  │FLStudioMCP   │  │ Piano Roll Script│ │
                        │  │(MIDI Ctrl)   │  │ (ComposeWithLLM) │ │
                        │  └──────────────┘  └──────────────────┘ │
                        └─────────────────────────────────────────┘
```

### How It Works

1. **Transport/Mixer/Channels/Plugins**:
   - The server writes the command, tagged with a unique id, to a JSON file
   - It sends a MIDI trigger note to FL Studio
   - FL's controller script reads the JSON, calls the FL API and writes a response echoing the id
   - The server reads the response with the matching id and ignores stale ones

2. **Piano Roll**:
   - The server queues note requests (each with an id) in a JSON file
   - It sends a keystroke (Ctrl+Alt+Y on Windows, Cmd+Opt+Y on macOS) to run FL's piano roll script. On Windows, the FL Studio window is brought to the front first
   - The piano roll script applies the requests and writes a response listing the ids it processed, which the server waits for

The server writes its JSON files atomically (temp file + rename, retried if the file is briefly locked). The FL-side scripts write in place, because `os.replace` is broken in FL Studio 2026's embedded Python (it breaks the interpreter for every later call). The server is safe against that: it ignores responses it can't parse yet and only accepts one carrying its own request id.

## Development

### Setup

```bash
uv sync --python 3.12   # installs runtime and dev dependencies
```

### Commands

| Command | Description |
|---------|-------------|
| `uv run fl-studio-mcp` | Run the MCP server |
| `uv run ruff check .` | Lint the codebase |
| `uv run ruff check --fix .` | Lint and auto-fix |
| `uv run pytest` | Run tests (FL Studio not required; the FL API is faked) |
| `uv run pytest --cov --cov-report=term-missing` | Run tests with coverage |

The FL-side scripts run inside FL Studio's embedded Python and can't import the server package. The tests load them against fake FL modules (`tests/fakes.py`). Changes to them still need a manual check in FL Studio.

### Project Structure

```
fl-studio-mcp/
├── fl_controller/
│   └── device_FLStudioMCP.py     # MIDI controller script (runs inside FL Studio)
├── scripts/
│   ├── ComposeWithLLM.pyscript   # Piano Roll script (runs inside FL Studio)
│   ├── install_mcp_for_claude.ps1 # Claude config installer (Windows)
│   ├── install_mcp_for_claude.sh # Claude config installer (macOS)
│   └── setup.sh                  # FL Studio script installer (macOS)
├── src/fl_studio_mcp/
│   ├── server.py                 # FastMCP server entry point
│   ├── music/                    # Genre-agnostic toolkit: model, theory, rhythm, transform
│   ├── tools/                    # MCP tools: channels, mixer, music, piano_roll, plugins, transport
│   └── utils/
│       ├── connection.py         # FL Studio connection wrapper
│       ├── fl_trigger.py         # Piano roll keystroke trigger
│       ├── midi_connection.py    # MIDI + JSON protocol, port selection
│       └── paths.py              # FL Settings folder lookup, atomic JSON writes
├── tests/                        # pytest suite with faked FL API
├── install.ps1                   # One-command installer (Windows)
└── install.sh                    # One-command installer (macOS)
```

## Credits

- [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp): the upstream project this fork is based on (see it for a demo video)
- [calvinw/fl-studio-mcp](https://github.com/calvinw/fl-studio-mcp): Piano Roll integration approach
- [FL Studio API Stubs](https://github.com/IL-Group/FL-Studio-API-Stubs): API documentation
- [FastMCP](https://github.com/jlowin/fastmcp): MCP server framework
- [mido](https://github.com/mido/mido): MIDI library for Python
- [Image-Line](https://www.image-line.com/): FL Studio

## License

MIT, see [LICENSE](LICENSE). The original copyright notice is retained.
