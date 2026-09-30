# FL Studio MCP Server

An MCP (Model Context Protocol) server that lets AI assistants control FL Studio (transport, mixer, channel rack, plugins and piano roll) through MIDI communication and Piano Roll scripts.

This is a fork of [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp). The goal is a **genre-agnostic toolkit** that any MCP-compatible AI can use to write any melody or style in FL Studio. The server provides precise, musically-expressed building blocks, and the AI makes the musical decisions. Hardcore/hardstyle is the first style used to test it; no genre logic lives in the server code.

> **Status:** early development. The foundation, music toolkit, FL control (tempo, patterns, targeted notes), roles and style packs are done; render and analysis is next. See [Roadmap](#roadmap).

## Features

### Project and Patterns

- One-call project overview: tempo, PPQ, time signature, patterns, channels, mixer track count, loop mode
- Get/set tempo (decimals allowed)
- List, select, rename patterns and find an empty one (selecting an unused pattern number creates it)

### Style Packs

- Optional JSON files describing a style in the toolkit's vocabulary: tempo range, scales, roles, song structure, rhythm grids, chord progressions and guidance
- Built in: hardstyle, hardcore and lo-fi hip hop (a deliberately different style, to keep the core generic); add your own in a folder of your choice
- Check whether the open project fits a style (tempo in range, required roles present)

### Roles

- Address a channel by a word in its name instead of its index: `"kick"` finds "808 Kick", `"sub bass"` finds "Sub Bass"
- Every note and step tool accepts a role wherever it takes a channel
- Check that a project has the parts a piece needs before writing (`fl_check_roles`)

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
- Per-step parameters: pitch, velocity, release, fine pitch, pan, mod X/Y and shift, in any pattern

### Plugin Control

- List plugin parameters
- Get/set parameter values
- Navigate presets (next/previous) and list their names
- Query plugin info

### Sounds from the Browser (Windows)

- **Browse** FL Studio's browser: stock packs, your own sample folders, presets, the plugin database
- **Load a sample, preset or instrument plugin** into a new channel, or into an existing one
- **Add effects** to any mixer track
- **Undo**, to take back a channel or effect
- None of this needs FL Studio in front: nothing is typed and the mouse isn't moved
- **Mix by numbers:** set a mixer track's level in dB, switch effects on and off, and render every channel on its own to see what it adds

### Piano Roll Control

- **Add notes** to the piano roll with precise timing
- **Add chords** with a single command
- **Delete specific notes** by MIDI number and time
- **Clear all notes** from the piano roll
- **Read piano roll state** to see all existing notes (fresh from FL, not a stale export)
- **Target any channel and pattern**: every note tool takes optional `channel` and `pattern`
- **Full note properties**: velocity, release, pan, slide, porta, muted, colour, filter cutoff/resonance and `fine_pitch` in cents
- Runs the piano roll script for you and reports what FL Studio applied. On Windows this goes through the piano roll's menu with FL Studio left in the background; on macOS FL Studio comes to the front for a keystroke (Cmd+Opt+Y)

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
- **FL Studio 2026 support:** the FL-side scripts no longer use `os.replace`, which breaks FL 2026's embedded Python.
- **More FL control:** tempo, project overview, patterns, per-step parameters, and piano roll notes aimed at a chosen channel and pattern with full note properties.
- **Things the API can't do (Windows):** loading samples, presets, instrument plugins and effects from FL's browser, and running the piano roll script, all without FL Studio coming to the front: the browser is driven through the API, and the few clicks and keys needed are posted to FL's own menus. Rendering to WAV goes through FL's own export dialog the same way. Every step is checked (the expected menu, dialog or window must be open) before the next one.
- **Arrangement and feedback:** `fl_arrange` lays a song out in the playlist (one playlist track per pattern, clips placed section by section), `fl_render` / `audio_analyze` measure the result (loudness, peaks, frequency balance, stereo image, key), and `fl_screenshot` shows FL's window.
- **Test suite:** pytest with the FL Studio API faked, so it runs without FL Studio. It includes a contract test that checks every tool against the FL-side controller script.

## Roadmap

1. **Foundation**: reliable protocol, tests, port and path handling. ✅
2. **Generic music toolkit** (pure Python): notes in musical units, scales and modes, chord symbols and roman numerals, scale-degree melodies, rhythm grids and Euclidean rhythms, rolls, transforms (arpeggiate, transpose, quantize, swing, humanize, ...). ✅
3. **More FL control**: tempo, project overview, pattern selection, per-step parameters, writing notes into a specific channel and pattern, and full note properties (slide, porta, pan, fine pitch). ✅
4. **Roles and templates**: refer to instruments by role (`"kick"`, `"lead"`, …) instead of channel numbers, based on a template project you prepare. ✅
5. **Style packs**: optional data files describing a genre (tempo range, structure, idioms); hardstyle, hardcore and lo-fi hip hop to start, the last one to keep the core generic. ✅
6. **Render and analysis**: render through FL's export dialog and give the AI feedback on loudness, peaks, frequency balance, stereo image and key. ✅

## Important Limitations

These come from FL Studio's scripting API, not from this server:

- **The API can't load anything or create channels.** On Windows the server does it through FL's browser instead (`fl_load_in_new_channel`, `fl_load_in_channel`, `fl_add_effect`, `fl_add_channel`): it moves the browser's cursor to the file and picks "Open in new channel" or "Send to selected channel" from the file's menu. That covers everything the browser shows, which excludes sounds that live inside a plugin's own preset browser (third-party synths, FLEX packs other than the loaded one). Channels and effects can't be deleted, only undone (`fl_undo`). On macOS none of this works: start from a template project that has the instruments you want.
- **Playlist clips are placed by `fl_arrange` only** (whole patterns on bar lines, the whole playlist at once, Windows); single clips can't be moved or read. **Automation can't be created.**
- **Loading and rendering are Windows-only, and you will see FL's menus and dialogs flash.** They never take the keyboard, the mouse or the focus, so you can keep working in another program. The loading tools close FL's open plugin windows (FL would otherwise load into the plugin whose window is open), leave the browser's folders closed, and un-minimize FL Studio if it is minimized (behind your other windows, without focus).
- **Time signature is read-only.** The overview reports it; set it in FL Studio yourself.
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

> **Piano-roll note:** on Windows the Piano Roll script is run from the piano roll's menu with FL Studio left in the background; you'll see that menu flash. If that isn't possible (no piano roll showing, or scripts kept in sub-folders) and on macOS, FL Studio is sent a keystroke instead (`Ctrl+Alt+Y` / `Cmd+Opt+Y`), which makes **FL Studio pop to the front for a moment**.

## Manual Installation

### 1. Install Python Dependencies

```bash
# Using uv (recommended; on Windows add --python 3.12)
uv sync
# Without the dev tools, add the audio analysis extra: uv sync --no-dev --extra analysis

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

After pulling changes to either script, copy it again; FL runs the installed copy, not the one in this repo. The piano roll script is re-read each time it runs. For the controller, reload it once from FL (View > Script output > Reload script); once it's installed, later updates can also be applied with the `dev.reloadScript` command (see Development), which makes it re-run its installed file.

### 4. Configure FL Studio

1. **Restart FL Studio** (if it's running) so it finds the new controller script
2. Go to **Options > MIDI Settings**
3. Under **Input**, select your virtual MIDI port (e.g. "loopMIDI Port" or "IAC Driver Bus 1")
4. Set **Controller type** to **FL Studio MCP Controller** (listed under *Scripts*)
5. Make sure **Enable** is lit for that port

The port only needs to be enabled under **Input**. Leave it alone under **Output**: the server sends to FL Studio, never the other way.

The piano roll tools need **Tools > Scripting > ComposeWithLLM** to have been run once per FL session from the piano roll window's own menu. On Windows the server does that itself the first time a note tool gets no answer; on macOS do it by hand (see [Piano Roll Workflow](#piano-roll-workflow)).

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
| `FL_MCP_STYLES_DIR` | Folder with your own style packs (`*.json`). A pack with the same name as a built-in one replaces it. |
| `FL_MCP_RENDER_DIR` | Folder `fl_render` writes to by default. The default is `fl-studio-mcp` in the system's temp folder. |
| `FL_MCP_SETTINGS_DIR` | Full path to FL Studio's `Settings` folder, if you changed FL's user data folder. The default is `<Documents>\Image-Line\FL Studio\Settings`. |

## Usage

### Running the Server Manually

```bash
uv run fl-studio-mcp
```

### Piano Roll Workflow

1. Open FL Studio and select a channel
2. Open the Piano Roll (F7 or double-click the channel)
3. On Windows the server runs the script itself, every time, from the piano roll's menu. On macOS the first run in each FL session is yours, because the shortcut only re-runs the last script: use the **piano roll's own menu** (the ▸ arrow in the piano roll window's top-left corner): **Tools > Scripting > ComposeWithLLM**. FL's main Tools menu in the top bar is a different menu and doesn't list piano roll scripts
4. The MCP tools then report what FL Studio applied
5. The script edits the channel the piano roll window shows, in the current pattern. Pass `channel` (an index or a role) and `pattern` to the note tools and they switch the piano roll there first. You'll see the window briefly close and reopen

### Style Packs

A style pack is a JSON file in `src/fl_studio_mcp/styles/` (built in) or in the folder named by `FL_MCP_STYLES_DIR` (yours). The file name must match its `name`. Packs are starting points for the AI, not rules, and nothing about any style is hard-coded in the server. Fields:

| Field | Content |
|-------|---------|
| `name`, `title`, `description` | Identifier (lowercase, `-` separated), display name, one-paragraph summary |
| `tempo` | `min`, `max`, `typical` BPM |
| `time_signature` | e.g. `"4/4"` |
| `scales` | Scale names from `music_reference` |
| `roles` | `required` and `optional` role names, matched against channel names like any role |
| `structure` | Sections with `section`, `bars`, `description` |
| `rhythms` | `name`, `role`, `grid` (music_rhythm grid syntax, 16 steps = one 4/4 bar of 16ths), `description` |
| `progressions` | `chords` (roman numerals or chord symbols), `scale`, `description` |
| `guidance` | List of plain-text tips |

Every pack is checked when loaded: unknown scales, grids that don't parse, progressions that don't resolve or rhythms for undeclared roles are reported by `style_list` instead of reaching the AI. Edits apply on the next call; no restart needed.

### Preparing a Template Project

On Windows the AI can load samples, presets, plugins and effects from FL's browser by itself, so a template is optional there: add your sample folders to the browser (Options > File settings) and it can use them. A template still saves time, is the only way on macOS, and is how to give the AI sounds the browser doesn't show (third-party synth presets). Set up a project once and reuse it:

1. Add a channel for each part you want the AI to write (drums, bass, lead, pads, effects…) and load your instruments and presets.
2. **Name each channel after its role**, such as "Kick", "Sub Bass" or "Lead". A role matches whole words in a name, ignoring case and punctuation: "Kick" finds "Kick" or "808 Kick", "bass" finds "Sub Bass" but not "Bassline". An exact name always wins, so if both "Kick" and "Kick Top" exist, "kick" means "Kick". Two channels that match equally (for example two named "Pad") are reported as ambiguous, never guessed.
3. Route each channel to its own mixer track if you want the AI to mix parts separately.
4. Save it as a template. FL Studio lists projects saved under `Documents\Image-Line\FL Studio\Projects\Templates\<Category>\<Name>\<Name>.flp` in **File > New from template**.

Ask the AI to run `fl_get_roles` to see what it can address, or `fl_check_roles` with the parts it needs.

## Available Tools

### Connection

| Tool | Description |
|------|-------------|
| `fl_connect` | Connect/reconnect to FL Studio |
| `fl_connection_status` | Get connection status and available MIDI ports |

### Project

| Tool | Description |
|------|-------------|
| `fl_get_project_overview` | Tempo, PPQ, time signature, patterns, channels, mixer track count, loop mode |
| `fl_get_tempo` | Get tempo in BPM |
| `fl_set_tempo` | Set tempo (10-522 BPM, decimals allowed) |

### Patterns

| Tool | Description |
|------|-------------|
| `fl_get_patterns` | List used patterns and the current one |
| `fl_select_pattern` | Select a pattern (an unused number creates it) |
| `fl_rename_pattern` | Rename a pattern (current one by default) |
| `fl_find_empty_pattern` | Lowest unused pattern number |

### Roles

| Tool | Description |
|------|-------------|
| `fl_get_roles` | Channels addressable by role, with mixer track and plugin |
| `fl_check_roles` | Which of a list of roles the project has (found / missing / ambiguous) |

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
| `fl_set_track_volume_db` | Set a mixer track's level in dB |
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
| `fl_list_generators` | Instrument plugins `fl_add_channel` can load (from FL's plugin database) |
| `fl_add_channel` | Add a channel with an instrument plugin, optionally named (Windows) |
| `fl_show_channel_editor` | Show/hide a channel's plugin window |
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
| `fl_set_step_params` | Turn steps on/off and set pitch, velocity, release, fine pitch, pan, mod X/Y, shift (optionally in a given pattern) |
| `fl_get_step_params` | Read steps back with their parameters (optionally from a given pattern) |

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
| `fl_list_presets` | Names of a plugin's own presets |
| `fl_next_preset` | Next preset |
| `fl_prev_preset` | Previous preset |
| `fl_get_plugin_color` | Get plugin color |

A channel's plugin can be addressed by role (`"lead"`) as well as by channel index; mixer effects are addressed by track number and slot.

### Browser and loading (Windows)

| Tool | Description |
|------|-------------|
| `fl_browser_search` | Find samples and presets by name in the folders FL's browser takes from disk ("kick 909", "sytrus pad"); returns paths the loading tools take |
| `fl_browser_list` | List a folder of FL's browser (`"Packs/Drums/Kicks"`, `"Plugin database/Effects"`, ...) |
| `fl_load_in_new_channel` | New channel from a sample, an instrument preset or a plugin, optionally named |
| `fl_load_in_channel` | Replace a channel's sample or preset, keeping its name |
| `fl_add_effect` | Add an effect plugin to a mixer track |
| `fl_get_track_effects` | Effects on a mixer track, by slot, with on/off and mix |
| `fl_set_effect` | Switch an effect on or off, or set how much of it is mixed in |
| `fl_close_message` | Close a message box FL is showing (FL ignores changes while one is open) |
| `fl_undo` | Step back in FL's undo history |

### Piano Roll

| Tool | Description |
|------|-------------|
| `fl_send_notes` | Add notes to the piano roll |
| `fl_send_chord` | Add a chord (multiple notes at same time) |
| `fl_delete_notes` | Delete specific notes |
| `fl_clear_piano_roll` | Clear all notes |
| `fl_get_piano_roll_state` | Read the piano roll's notes, fresh from FL |

These five take optional `channel` (an index or a role) and `pattern`. Notes can carry `velocity`, `release`, `pan`, `fcut`, `fres` (all 0.0-1.0), `slide`, `porta`, `muted`, `color` and `fine_pitch` (cents, ±1200; read back as `pitchofs` in tens of cents).
| `fl_trigger_script` | Manually trigger the FL Studio script for queued requests |
| `fl_get_piano_roll_info` | Get piano roll system info |
| `fl_clear_request_queue` | Cancel pending queued changes |

### Arrangement, Render and Analysis

| Tool | Description |
|------|-------------|
| `fl_arrange` | Build the song in the playlist: sections back to back, each pattern on its own playlist track, repeated to fill its sections. FL ends in song mode (Windows) |
| `fl_get_playlist` | Song length and the names of the first playlist tracks |
| `fl_arrange_in_pattern` | The older way: the song written as notes into one long pattern (works without the playlist) |
| `fl_screenshot` | A picture of FL's window or one panel (playlist, piano roll, channel rack, mixer, browser), taken in the background (Windows) |
| `fl_render` | Render what FL would play (pattern or song mode) to a WAV through FL's export dialog, and measure it (Windows) |
| `fl_render_stems` | Render each channel soloed and summarise it (loudness, peak, clipping, energy per band, pitch), to balance the mix; `master_effects=false` takes the master's limiter out of the measurement (Windows) |
| `fl_measure_pitch` | Play one note on a channel alone and report the pitch that comes out, and how to transpose its notes so it is in tune with the song (Windows) |
| `audio_analyze` | Measure any WAV: loudness (LUFS, ITU-R BS.1770), peak, RMS, crest factor, clipping, silence, energy per frequency band, stereo correlation and width, estimated key, the note played most (pitch), loudness over time |

`audio_analyze` needs numpy (`uv sync` installs it with the dev tools; otherwise `uv sync --extra analysis`). The key estimate often can't tell a key from its relative (E minor / G major), so it reports a runner-up.

### Styles

| Tool | Description |
|------|-------------|
| `style_list` | Available style packs (and any that failed to load, with the reason) |
| `style_get` | A full style pack |
| `style_check_project` | Does the open project fit a style: tempo in range, required roles found |

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
"Set the tempo to 150, find an empty pattern, call it 'Intro' and put a bassline on channel 3 there"
"Check the project has a kick, a bass and a lead, then write a 4-bar bassline on the bass"
"Using the hardstyle style pack, check my project and write a 16-bar climax: kick on every beat tuned to F, and a lead melody on the lead channel"
"Write a 16-step hi-hat pattern on channel 2 with accents on the off-beats and the 4th step panned left"
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
2. The script must have been run once per FL session from the piano roll window's menu (**Tools > Scripting > ComposeWithLLM**, not FL's main Tools menu). On Windows the server tries this itself; if a dialog or another open menu was in the way, close it and retry, or run it by hand
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
| `uv run python -c "from fl_studio_mcp.utils.connection import call; print(call('dev.reloadScript'))"` | Make the running FL controller re-run its installed file after you copy a new version in |

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
