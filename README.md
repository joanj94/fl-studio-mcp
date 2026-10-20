# FL Studio MCP Server

[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)](https://modelcontextprotocol.io)
[![FL Studio](https://img.shields.io/badge/FL%20Studio-20.7%2B%20%C2%B7%20tested%20on%202026-orange)](https://www.image-line.com/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant (Claude, Cursor, Gemini CLI, Codex, ...) **produce music in FL Studio**. It can write notes and drum patterns, load sounds and effects from the browser, lay the song out in the playlist, automate parameters, mix, render, and listen back to its own render.

It offers **138 tools**. They work on the FL Studio window you already have open, and FL can stay in the background while they run.

```text
You:    "Make a 16-bar F minor intro at 150 BPM: a kick on every beat, a sub bass on the
         root, and a lead playing i-VI-III-VII. Arrange it, render it and check the mix."

The AI: fl_set_tempo → fl_browser_search "kick" → fl_load_in_new_channel → music_chords
        → music_transform (arpeggiate) → fl_send_notes → fl_arrange → fl_render_stems
        → fl_diagnose_mix → fl_gain_stage → fl_render "intro.mp3"
```

This is a fork of [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp) (see it for a demo video). The core is **genre-agnostic**: it supplies precise musical building blocks, and the AI makes the musical decisions. Genre knowledge lives only in optional JSON *style packs*.

---

## Contents

- [What it can do](#what-it-can-do)
- [Platform support](#platform-support)
- [Quick start](#quick-start)
- [Manual installation](#manual-installation)
- [Using it](#using-it)
- [Tool reference](#tool-reference)
- [How it works](#how-it-works)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits](#credits)

## What it can do

| Area | Highlights |
|------|-----------|
| **Composing** | Piano roll markers (key/scale, time signature, pattern length) and the project's time signature. Notes, chords and drum steps in any channel and pattern, with every note property (slide, porta, pan, fine pitch, velocity, filter). There is also a pure-Python music toolkit: scales, chord symbols, roman numerals, smooth voice leading, basslines from chords, MIDI file import, scale-degree melodies, step grids, Euclidean rhythms, rolls, and transforms (arpeggiate, strum, flam, chop, accents, velocity curves, swing, humanize, quantize, fix overlaps, groove templates, ...). |
| **Sounds** | Searches FL's browser by name, or by the tempo and key written in sample names ("128bpm", "F#m"), loads samples, presets and instrument plugins into new or existing channels, adds and deletes mixer effects, and deletes channels. |
| **Arranging** | Lays the song out in the playlist (one track per pattern, section by section), adds or deletes single clips, adds time markers, clones patterns, and exports MIDI. |
| **Automation** | Creates automation clips for volume, pan or *any* plugin parameter, with the points you give it. It can also read the automation and clips stored in a saved project. |
| **Mixing** | Sets levels in dB and parameters by their display value ("-6 dB", "2.5 kHz"). Applies named moves ("remove_mud", "glue", "dotted_echo") and builds whole effect chains. Mutes or solos groups of tracks by name, routes tracks to buses, and keeps mix snapshots to restore later. |
| **Listening** | Renders to WAV or MP3 through FL's own export dialog, then measures the result: loudness (ITU-R BS.1770 LUFS), peaks, frequency balance, stereo image, key and tempo. It renders per-channel stems, reports mix problems, gain-stages the tracks, masters the song to a loudness target with a limiter, matches its EQ to a reference track, reads the live meters, compares the mix with a reference track, and measures a channel's pitch to tune it. |
| **Audio in** | Splits a mixed song into vocals, drums, bass and the rest (Demucs, on your NVIDIA GPU when there is one), turns a recorded melody into notes, finds the tempo of an audio file, and slices samples or vocals at their onsets into WAVs ready to load. Sung vocals can be rendered from notes plus lyrics through a local [OpenUtau](https://www.openutau.com/) engine. |
| **FL's own tools** | With FL started by `fl_launch`: FL 2026's built-in assistant tools (48 of them) are used for channels, effects and the piano roll, and can be called directly. Any function of FL's scripting API can be called as well. |
| **Seeing** | Takes screenshots of FL's window or of one panel (playlist, piano roll, mixer, ...), taken in the background. |

The scripting API of FL Studio can't load plugins, place clips, create automation or delete effects. On Windows this server does those things anyway: it drives FL's own browser and menus with **posted window messages**. It never takes your mouse, keyboard or focus. Each step is checked (the expected menu, dialog or result must appear) before the next one runs.

## Platform support

| Feature | Windows | macOS |
|---------|:-------:|:-----:|
| Transport, mixer, channels, plugins, patterns, steps | ✅ | ✅ |
| Piano roll notes | ✅ background | ✅ FL comes to the front briefly |
| Music toolkit, style packs, MIDI export, audio analysis | ✅ | ✅ |
| Stem separation | ✅ NVIDIA GPU or CPU | ✅ Apple GPU or CPU |
| Browser loading, effects, channel deletion | ✅ | ❌ start from a template project |
| Playlist arrangement, clips, automation, render, screenshots, save | ✅ | ❌ |

Everything was developed and tested live on **FL Studio 2026 (Windows)**, including the trial edition.

## Quick start

**You need:** FL Studio 20.7+, [uv](https://github.com/astral-sh/uv) (the installer gets it), and a virtual MIDI port: [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html) on Windows, or the built-in IAC Driver on macOS.

```powershell
# Windows (PowerShell)
git clone https://github.com/joanj94/fl-studio-mcp.git
cd fl-studio-mcp
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned -Force   # one-time
.\install.ps1
```

```bash
# macOS
git clone https://github.com/joanj94/fl-studio-mcp.git
cd fl-studio-mcp
./install.sh
```

The installer installs the dependencies and walks you through creating the MIDI port. It also copies the two FL-side scripts into FL Studio's Settings folder and registers the server with Claude Desktop or Claude Code.

**Then, in FL Studio:**

1. Restart FL Studio.
2. Open **Options > MIDI Settings**. Under **Input**, select your virtual port, set **Controller type** to **FL Studio MCP Controller** and make sure it is enabled. Leave **Output** alone.
3. Ask your AI: *"Give me an overview of the open FL Studio project."*

**Recommended (FL Studio 2026, Windows):** let the AI start FL with `fl_launch` instead of starting it yourself. FL then runs with its own built-in assistant tools reachable, and channels, effects and piano roll notes go through those: no menu walks, and notes about ten times faster. Everything still works without it, through FL's menus.

### Other MCP clients

Any MCP client that speaks stdio works. Point it at:

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

### Optional extras

| For | Install |
|-----|---------|
| Audio measurement (`fl_render`, `audio_analyze`, stems, mix check) | numpy: `uv sync --extra analysis` (included with the dev tools) |
| Reading MP3 / FLAC / OGG files | [ffmpeg](https://ffmpeg.org/) on PATH, or `FL_MCP_FFMPEG` |
| Sung vocals (`vocal_render`) | an `ourender` build of OpenUtau's engine plus a singer, on PATH or `FL_MCP_OURENDER` |
| Stem separation (`audio_separate_stems`) | Demucs and torch: `.\install.ps1 -Stems auto` / `./install.sh --stems`, see [Stem separation](#stem-separation) |

### Stem separation

`audio_separate_stems` splits a mixed song (WAV, MP3, FLAC, ...) into **vocals, drums, bass and other** with [Demucs](https://github.com/adefossez/demucs) (`htdemucs_6s` adds guitar and piano). The stems land as WAVs in the browser folder *Project bones/FL-MCP stems/&lt;song&gt;*, ready to load into a channel. Use it for an acapella to sample, a drum break, a bassline to transcribe, or to study a reference track part by part.

**Install** (pick one; about 2.5 GB with CUDA, 300 MB for the CPU build):

```powershell
.\install.ps1 -Stems auto      # Windows: GPU build if nvidia-smi is found, else CPU
./install.sh --stems            # macOS / Linux: the same
```

Or by hand: `uv sync --python 3.12 --extra stems-gpu` (NVIDIA GPU, CUDA 12.6 build of torch) or `uv sync --python 3.12 --extra stems` (CPU only). The two extras can't be combined. A plain `uv sync` (or the installer without the option) removes them again, so repeat the option when you update. Restart your MCP client afterwards.

**CPU or GPU** is chosen on every run (`device="auto"`, the default):

1. An NVIDIA GPU, if torch was built with CUDA and the card passes a small test run. A torch build that lacks the card's kernels reports CUDA but then fails, so it is tested rather than trusted.
2. Apple's GPU (MPS) on Apple Silicon Macs.
3. Otherwise the CPU.

A GPU that runs out of memory falls back to the CPU on its own. Force a device with `device="cuda"`, `"mps"` or `"cpu"`. `audio_stem_devices` tells you which device would be used and why, without separating anything.

| | 3.6-minute song, `htdemucs` |
|--|--|
| GPU (GTX 1080 Ti) | 6.7 s |
| CPU | 73 s |

The first run of each model downloads it (about 80 MB, cached by Hugging Face). Options: `two_stems="vocals"` writes only *vocals* and *no_vocals*. `model="htdemucs_ft"` is a little cleaner and four times slower. `shifts=2..10` averages several runs, which is cleaner and slower. Stems are what the model hears: some bleed between them is normal, and a stem whose `level_db` is around -45 dB or lower is close to empty (an instrumental's vocals).

## Manual installation

<details>
<summary>Step by step, without the installer</summary>

### 1. Python dependencies

```bash
uv sync                 # on Windows: uv sync --python 3.12 (python-rtmidi ships 3.12 wheels)
# or: pip install -e ".[analysis]"
```

### 2. Virtual MIDI port

- **Windows:** install loopMIDI (`winget install TobiasErichsen.loopMIDI`) and click **+** to create a port. The default name works. Keep loopMIDI running; it can start with Windows.
- **macOS:** open **Audio MIDI Setup**, then **Window > Show MIDI Studio**. Double-click **IAC Driver**, tick **Device is online** and click **Apply**.

### 3. FL-side scripts

Copy both files into `<Documents>\Image-Line\FL Studio\Settings`. On Windows, Documents may be redirected to OneDrive; the installer handles that for you.

| File | Destination inside `Settings` |
|------|-------------------------------|
| `fl_controller/device_FLStudioMCP.py` | `Hardware/FLStudioMCP/` |
| `scripts/ComposeWithLLM.pyscript` | `Piano roll scripts/` |

```powershell
$settings = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Image-Line\FL Studio\Settings"
New-Item -ItemType Directory -Force "$settings\Hardware\FLStudioMCP" | Out-Null
Copy-Item fl_controller\device_FLStudioMCP.py "$settings\Hardware\FLStudioMCP\"
Copy-Item scripts\ComposeWithLLM.pyscript "$settings\Piano roll scripts\"
```

FL runs the installed copies, not the files in this repo, so copy them again after you update. The piano roll script is re-read on every run. Reload the controller once with **View > Script output > Reload script**; after that, the `dev.reloadScript` command reloads it (see [Development](#development)).

### 4. FL Studio and the MCP client

Configure FL Studio as described in [Quick start](#quick-start). For Claude Desktop, add the JSON above to `%APPDATA%\Claude\claude_desktop_config.json` (Windows) or `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS). For Claude Code, use `claude mcp add`.

</details>

### Environment variables

Set these in the server entry's `env` block if you need them.

| Variable | Purpose |
|----------|---------|
| `FL_MCP_MIDI_PORT` | The MIDI port to use (its name or part of it). Without it, the server only picks a port named like `loopMIDI`, `IAC` or `FL`, and never guesses beyond that. |
| `FL_MCP_SETTINGS_DIR` | FL Studio's `Settings` folder, if you moved FL's user data folder. |
| `FL_MCP_RENDER_DIR` | The default folder for `fl_render`. |
| `FL_MCP_STYLES_DIR` | A folder of your own style packs (`*.json`). A pack with a built-in pack's name replaces it. |
| `FL_MCP_FFMPEG` | The ffmpeg program, if it isn't on PATH. |
| `FL_MCP_OURENDER` | The `ourender` program for sung vocals. |
| `FL_MCP_FL_EXE` | FL Studio's program for `fl_launch`, if the installer's registry entry isn't right. |
| `FL_MCP_GOPHER_PORT` | The debugging port `fl_launch` opens for FL's own assistant (default 9222). |
| `FL_MCP_PROFILE` | Offer only some tool groups, for clients that struggle with many tools: `minimal` (73 tools), `compose` (122), `mix` (128), `analysis` (37), or several joined by commas (`compose,mix`). Default `all` (174). `fl_batch` (several tool calls in one) is always there. |

## Using it

### Prepare a project (optional on Windows)

On Windows the AI can load sounds from FL's browser by itself. To let it use your own sample folders, add them to the browser (**Options > File settings**). A template project still saves time. It is the only way to give the AI sounds on macOS, and it is how you give it sounds the browser doesn't show (third-party synth presets).

**Name channels after their role**, such as "Kick", "Sub Bass" or "Lead". Every tool that takes a channel also accepts a role: `"kick"` finds "808 Kick". Matching uses whole words and ignores case. An exact name wins, and if two channels match equally the tool reports it rather than guessing. `fl_get_roles` lists what the AI can address.

### Example prompts

```text
"Give me an overview of the project, then check it has a kick, a bass and a lead."
"Find a 909 kick in the browser and load it into a new channel called Kick."
"Write an F minor i-VI-III-VII progression, arpeggiate it in 16ths over two octaves, put it on the lead."
"Add a snare roll over the last 2 bars that speeds up from 8ths to 32nds and gets louder."
"Arrange: intro 8 bars (pattern 1), build 8 bars (patterns 1+2), drop 16 bars (all patterns)."
"Fade the lead's reverb mix in over the build with an automation clip."
"Put a Parametric EQ on the bass track and remove the mud; then glue the drum bus."
"Render the stems, tell me what clashes, gain-stage the mix and export the song as song.mp3."
"Take a snapshot called 'before master', try a louder master, and restore it if it clips."
"Here is my vocal recording: extract the melody and double it with the lead."
"Pull the acapella out of reference.mp3 and load it into a new channel."
```

### Style packs

A style pack is a JSON file describing a style in the toolkit's vocabulary: tempo range, scales, roles, song structure, rhythm grids, chord progressions and tips. Built-in packs live in `src/fl_studio_mcp/styles/`: hardstyle, hardcore, hardjump, jumpstyle, lento violento, and lo-fi hip hop (the last is deliberately unrelated, to keep the core generic). Add your own through `FL_MCP_STYLES_DIR`.

Each pack is validated when it loads. Unknown scales, grids that don't parse, and rhythms for undeclared roles are reported by `style_list` instead of reaching the AI. Packs are read again on every call, so edits need no restart.

<details>
<summary>Style pack fields</summary>

| Field | Content |
|-------|---------|
| `name`, `title`, `description` | Identifier (lowercase, `-` separated, equal to the file name), display name, summary |
| `tempo` | `min`, `max`, `typical` BPM |
| `time_signature` | e.g. `"4/4"` |
| `scales` | Scale names from `music_reference` |
| `roles` | `required` and `optional` role names |
| `structure` | Sections with `section`, `bars`, `description` |
| `rhythms` | `name`, `role`, `grid` (`music_rhythm` syntax, 16 steps = one bar of 16ths), `description` |
| `progressions` | `chords` (roman numerals or chord symbols), `scale`, `description` |
| `guidance` | Plain-text tips |

</details>

Pitch names are scientific: **C4 = MIDI 60**. FL's piano roll labels the same note C5.

## Tool reference

The server has 174 tools in total (set `FL_MCP_PROFILE` to offer fewer). Expand a group to see its tools. Tools marked **(W)** are Windows only.

<details>
<summary><b>FL's own assistant and raw API</b> (6)</summary>

| Tool | Description |
|------|-------------|
| `fl_launch` | Start FL Studio with its own assistant tools reachable (recommended on FL 2026) **(W)** |
| `fl_assistant_status` / `fl_assistant_call` | List FL's own 48 assistant tools; run one of them |
| `fl_run_piano_roll_python` | Run Python with the full `flpianoroll` API in a piano roll (selection, groups, ...) |
| `fl_api_call` / `fl_api_list` | Call any function of FL's MIDI scripting API; list a module's functions and constants |

</details>

<details>
<summary><b>Markers, time signature, built-in EQ, sections, slicing, presets, MIDI import, chords, drums</b> (19)</summary>

| Tool | Description |
|------|-------------|
| `fl_add_piano_roll_marker` / `fl_get_piano_roll_markers` / `fl_clear_piano_roll_markers` | Key/scale, time signature, pattern length and named markers in a pattern's piano roll (needs `fl_launch`) |
| `fl_set_time_signature` | The project's time signature (3/4, 6/8, 7/8, ...) |
| `fl_redo` / `fl_get_undo_history` | Redo after `fl_undo`; position in the undo history and unsaved changes |
| `fl_get_track_eq` / `fl_set_track_eq` | The mixer's built-in 3-band EQ in dB and Hz, no plugin needed |
| `fl_set_playlist_selection` / `fl_get_playlist_selection` | A time selection over some bars (loop one section); `fl_render(start_bar, end_bar)` renders just those bars |
| `audio_masking` | Where two stems compete for the same frequencies |
| `audio_slice` | Cut a sample or vocal at its onsets into WAVs in the browser |
| `fl_select_preset` | Switch a plugin to one of its own presets by name |
| `fl_get_drum_pads` | An FPC's pads and the note each plays |
| `fl_set_pattern_color` | Colour a pattern (its playlist clips follow) |
| `music_import_midi` | Read a .mid file into notes ready for `fl_send_notes`, one list per part |
| `music_bassline` | A bassline under chords: a step grid on the roots, fifths or octaves |
| `audio_chords` | The chords of a recording bar by bar (a reference's progression to write against) |
| `audio_drums` | A clean drum loop as kick/snare/hat hits, grids and a groove template (`music_transform` op `groove`) |

</details>

<details>
<summary><b>Project, patterns and roles</b> (14)</summary>

| Tool | Description |
|------|-------------|
| `fl_connect` / `fl_connection_status` | Connect to FL Studio; list the MIDI ports the server sees |
| `fl_get_project_overview` | Tempo, PPQ, time signature, patterns, channels, mixer tracks, loop mode: a good first call |
| `fl_get_tempo` / `fl_set_tempo` | Read or set the tempo (decimals allowed) |
| `fl_get_patterns` | Patterns in use and the active one |
| `fl_select_pattern` | Make a pattern active (an unused number creates it) |
| `fl_rename_pattern` | Rename a pattern |
| `fl_find_empty_pattern` | First unused pattern |
| `fl_clone_pattern` | Copy a pattern's notes and steps, e.g. for a variation |
| `fl_add_marker` / `fl_get_markers` | Named time markers on the playlist |
| `fl_get_roles` / `fl_check_roles` | Channels addressable by role; check that the parts a piece needs exist |

</details>

<details>
<summary><b>Notes: piano roll and step sequencer</b> (16)</summary>

| Tool | Description |
|------|-------------|
| `fl_send_notes` | Add or replace notes, in any channel (index or role) and pattern |
| `fl_send_chord` | Add a chord |
| `fl_delete_notes` / `fl_clear_piano_roll` | Delete specific notes, or all of them |
| `fl_get_piano_roll_state` | Read the notes, fresh from FL |
| `fl_quantize_notes` | Snap a channel's notes to a grid |
| `fl_trigger_script` / `fl_clear_request_queue` / `fl_get_piano_roll_info` | Run, cancel or inspect queued piano roll requests |
| `fl_get_grid_bit` / `fl_set_grid_bit` | Read or set one step |
| `fl_get_step_sequence` / `fl_set_step_sequence` | Read or set a channel's steps |
| `fl_set_step_params` / `fl_get_step_params` | Steps with per-step pitch, velocity, release, fine pitch, pan, mod X/Y and shift |
| `fl_trigger_note` | Play a note live (not stored unless FL records) |

Notes can carry `velocity`, `release`, `pan`, `fcut`, `fres` (0-1), `slide`, `porta`, `muted`, `color` and `fine_pitch` (cents).

</details>

<details>
<summary><b>Channels and sounds</b> (25)</summary>

| Tool | Description |
|------|-------------|
| `fl_get_channel_count` / `fl_get_channel_info` / `fl_get_all_channels` / `fl_get_selected_channel` | Read the channel rack |
| `fl_select_channel` / `fl_select_one_channel` | Select channels |
| `fl_set_channel_volume` / `fl_set_channel_pan` / `fl_set_channel_name` / `fl_set_channel_color` | Channel properties |
| `fl_mute_channel` / `fl_solo_channel` | Mute or solo |
| `fl_route_channel_to_mixer` | Send a channel to a mixer track |
| `fl_show_channel_editor` | Show or hide a channel's plugin window |
| `fl_browser_search` | Find samples and presets by name across the browser's folders, in under a second; filter by the tempo or key in their names (`bpm=140`, `key="Am"`, relative keys too) |
| `fl_browser_list` | List a browser folder (`"Packs/Drums/Kicks"`, `"Plugin database/Generators"`, ...) **(W)** |
| `fl_browser_refresh` | Make FL read its browser folders from disk again **(W)** |
| `fl_load_in_new_channel` | A new channel from a sample, preset or plugin **(W)** |
| `fl_load_in_channel` | Replace a channel's sound, keeping its name **(W)** |
| `fl_list_generators` / `fl_add_channel` | Instrument plugins FL has, and a new channel with one **(W)** |
| `fl_delete_channels` | Delete channels with their notes **(W)** |
| `fl_set_cut_itself` | Make new notes cut ones still sounding **(W)** |
| `fl_close_message` / `fl_undo` | Close an FL message box; step back in FL's undo history |

</details>

<details>
<summary><b>Plugins and effects</b> (16)</summary>

| Tool | Description |
|------|-------------|
| `fl_is_plugin_valid` / `fl_get_plugin_name` / `fl_get_plugin_color` | Plugin info |
| `fl_get_plugin_param_count` / `fl_get_plugin_params` | Parameters with current values (searchable by name) |
| `fl_get_plugin_param_value` / `fl_set_plugin_param_value` | Raw 0-1 values |
| `fl_set_plugin_param_by_value` | Set what the knob should show: `"-6 dB"`, `"2.5 kHz"`, `"1:4"`, `"Peaking"` |
| `fl_get_preset_count` / `fl_list_presets` / `fl_next_preset` / `fl_prev_preset` | A plugin's own presets |
| `fl_add_effect` / `fl_delete_effect` | Add or remove a mixer effect **(W)** |
| `fl_get_track_effects` / `fl_set_effect` | A track's effects; switch one on or off or set its mix |

Plugins are addressed by channel (index or role), or by mixer track and slot.

</details>

<details>
<summary><b>Mixer and mixing</b> (30)</summary>

| Tool | Description |
|------|-------------|
| `fl_get_mixer_track_count` / `fl_get_mixer_track_info` / `fl_get_all_mixer_tracks` | Tracks in use, with level and the channels routed to them |
| `fl_set_track_volume` / `fl_set_track_volume_db` / `fl_set_track_pan` / `fl_set_stereo_separation` | Levels (also in dB), pan and width |
| `fl_mute_track` / `fl_solo_track` / `fl_arm_track` | Mute, solo or arm one track |
| `fl_set_track_name` / `fl_set_track_color` | Name and colour |
| `fl_route_track` | Send a track to a bus, or add a send with a level |
| `fl_mute_tracks` / `fl_solo_tracks` / `fl_clear_mute_solo` | Groups by name (`"drums"`) or number; solo keeps buses and sources playing |
| `fl_set_colors` | Colour tracks and channels by name or hex |
| `fl_list_intents` / `fl_apply_intent` | Named moves: `remove_mud`, `add_air`, `glue`, `large_hall`, `dotted_echo`, ... scaled by `amount` |
| `fl_setup_chain` | Build a chain on a track: `vocal`, `lead`, `pad`, `bass`, `drum_bus`, `master` **(W)** |
| `fl_diagnose_mix` | Render the mix and every part and list what to check: clipping, headroom, buried parts, masking, low end, routing **(W)** |
| `fl_gain_stage` | Level every track so each part peaks at the same level **(W)** |
| `fl_save_chain` / `fl_apply_chain` / `fl_list_chains` | Keep a track's effect chain (plugins, order, every parameter) under a name and put it on any track **(W)** |
| `fl_master_to_loudness` | Bring the song to a loudness (e.g. -14 LUFS): the master's Fruity Limiter, its ceiling, then its gain render by render **(W)** |
| `fl_get_track_level` | The meters' highest peak while FL plays |
| `fl_watch_peaks_start` / `fl_watch_peaks_status` / `fl_watch_peaks_stop` | Watch the meters in the background through a whole playthrough |
| `fl_take_snapshot` / `fl_restore_snapshot` / `fl_list_snapshots` | Keep the whole mix (levels, routing, effects and their parameters, channels) and restore only what changed |
| `fl_find_unused` | What the project no longer uses; changes nothing |

</details>

<details>
<summary><b>Arrangement, automation and project files</b> (10)</summary>

| Tool | Description |
|------|-------------|
| `fl_arrange` | Build the song in the playlist from sections: one playlist track per pattern, mutes kept with their pattern **(W)** |
| `fl_add_clip` / `fl_delete_clip` | One clip, with the others left alone **(W)** |
| `fl_get_playlist` | Song length and track names |
| `fl_arrange_in_pattern` | The older way: the song written into one long pattern |
| `fl_create_automation` | An automation clip with your points for a volume, pan or any plugin parameter **(W)** |
| `fl_read_project` | Automation and clips from the project's last save or autosave |
| `fl_save_project` | File > Save, confirmed by the file on disk **(W)** |
| `fl_export_midi` | A pattern, or the song given as sections, as a `.mid` file |
| `fl_screenshot` | A PNG of FL or one panel, taken in the background **(W)** |

</details>

<details>
<summary><b>Transport</b> (8)</summary>

`fl_play`, `fl_stop`, `fl_record`, `fl_get_transport_status`, `fl_set_song_position`, `fl_get_song_length`, `fl_set_loop_mode` (pattern or song), `fl_set_playback_speed`.

</details>

<details>
<summary><b>Render and audio analysis</b> (10)</summary>

| Tool | Description |
|------|-------------|
| `fl_render` | Render to WAV or MP3 through FL's export dialog, and measure a WAV **(W)** |
| `fl_render_stems` | Render every channel on its own and summarise each one, optionally with the master's limiter taken out **(W)** |
| `fl_measure_pitch` | Play one note on a channel and report its real pitch, plus how to transpose the notes into tune **(W)** |
| `audio_analyze` | Any audio file: LUFS, peak, RMS, crest factor, clipping, spectrum bands, stereo width, key, tempo, loudness over time |
| `audio_compare` | Your mix next to a reference track |
| `audio_loop_tempo` | A loop's exact tempo from its length (a whole number of bars), picked by the number in its name or the tempo heard |
| `audio_qc` | Technical checks before release: DC offset, true peak (4x oversampled), clicks, dropouts, out-of-phase seconds, mono loss |
| `audio_match_eq` | EQ moves (frequency, gain, peaking or shelf) toward a reference track's tonal balance |
| `fl_match_eq` | Render the song, propose those moves, and with `apply=true` set them on a Parametric EQ 2 (master by default) **(W)** |
| `audio_extract_melody` | A recorded single-voice melody turned into notes |
| `audio_separate_stems` | Split a song into vocals, drums, bass and other (or 6 stems) with Demucs, GPU when available, as WAVs the browser can load ([setup](#stem-separation)) |
| `audio_stem_devices` | Which device (CUDA GPU, Apple GPU or CPU) stem separation would use, and why |
| `vocal_render` / `vocal_list_singers` | Sing notes with lyrics through OpenUtau's engine, as a WAV the browser can load |

</details>

<details>
<summary><b>Music toolkit and styles</b> (11, no FL needed)</summary>

| Tool | Description |
|------|-------------|
| `music_reference` | The vocabulary: scales, chord qualities, transforms, grid syntax |
| `music_scale` / `music_chords` / `music_degrees` | Scales; progressions from `Fm7`, `C/E` or `i-VI-III-VII`; melodies as scale degrees |
| `music_rhythm` / `music_roll` | Step grids (`X...x...`), Euclidean rhythms (`E(3,8)`), rolls that speed up |
| `music_transform` | Arpeggiate, transpose, shift, stretch, reverse, quantize, swing, humanize, snap to scale, legato, repeat, velocity ramp |
| `music_export_midi` | Notes as a `.mid` file |
| `style_list` / `style_get` / `style_check_project` | Style packs, and whether the project fits one |

</details>

## How it works

```text
┌──────────────┐ stdio ┌────────────────────────────────────────────────────────┐
│  MCP client  │──────▶│ FastMCP server (src/fl_studio_mcp)                     │
└──────────────┘       │  tools/ ─ music/ ─ audio/ ─ project/ (pure Python)     │
                       └───┬───────────────────┬───────────────────┬────────────┘
             JSON command + │      JSON queue + │     posted window │  WAV / FLP
              MIDI trigger  │       menu click  │   messages (Win)  │  files
                           ▼                   ▼                   ▼
                       ┌────────────────────────────────────────────────────────┐
                       │ FL Studio                                              │
                       │  FL Studio MCP Controller   ComposeWithLLM   browser,  │
                       │  (MIDI script, FL API)      (piano roll)     menus     │
                       └────────────────────────────────────────────────────────┘
```

1. **Controller commands** (transport, mixer, channels, plugins, patterns). The server writes `{"id", "action", "params"}` to a JSON file and sends a MIDI note over the virtual port. The controller script inside FL calls FL's API and writes a response that echoes the id. Stale or half-written responses are ignored, and only one command is in flight at a time.
2. **Piano roll.** Requests are queued in a JSON file. On Windows the server runs the `ComposeWithLLM` script from the piano roll's own menu while FL stays in the background; on macOS it sends a keystroke. The script validates the whole batch, applies it, and confirms which requests it processed.
3. **What the API can't do** (Windows). Loading, clips, automation, effect deletion, render and save are done by posting clicks and keys to FL's own windows and menus. Every step waits for the expected menu, dialog or result before the next one, and many are checked against a fresh read or a picture of the panel.
4. **FL's own assistant** (FL 2026, started with `fl_launch`). FL ships an MCP server of its own behind its Gopher panel. The server reaches it over WebView2's debugging port and uses it to add and delete channels and effects by name, to run piano roll Python (notes, markers) in about 0.05 s, and for FL's other tools. Every change is still checked by reading it back.
5. **Analysis** runs in the server, on files: rendered WAVs (numpy), saved `.flp` projects, and `.fst` presets.

## Limitations

- **macOS** only has the API-level tools: loading, playlist, automation, render and screenshots need Windows. On macOS, start from a template project that has your instruments.
- **FL's menus and dialogs flash** while a background tool runs. Your keyboard, mouse and focus are never taken, but don't click inside FL while a tool is driving it.
- **Clips can't be read** from the running project. `fl_read_project` reads the last save or autosave instead, and `fl_screenshot` shows the playlist.
- **Sounds inside a plugin's own preset browser** (third-party synths) can't be loaded by name. Put them in a template project.
- **The time signature is read-only**, and time markers can't be deleted through the API.
- **Mixer sidechain inputs can't be set.** Use a bus-based pump instead.
- **In the FL trial**, saved projects can't be reopened and their values are scrambled. Everything else works, plugins outside the edition included (they run as demos).

## Troubleshooting

<details>
<summary><b>"Not connected to FL Studio" / "No virtual MIDI port found"</b></summary>

1. Make sure loopMIDI is running (Windows) or the IAC Driver is online (macOS).
2. Run `fl_connection_status` to see the ports the server can see. If yours isn't named like `loopMIDI`, `IAC` or `FL`, set `FL_MCP_MIDI_PORT`.
3. In FL's MIDI Settings, check that **FL Studio MCP Controller** is set on that port under **Input**, then restart FL Studio.

</details>

<details>
<summary><b>"Timeout waiting for FL Studio response"</b></summary>

1. FL Studio must be running, with the controller enabled on the port the server uses.
2. Look for errors from FLStudioMCP under **View > Script output**.
3. Copy `fl_controller/device_FLStudioMCP.py` again after updating: an old copy doesn't echo request ids, so every command times out.
4. If an FL message box is open, FL ignores changes; the server refuses to send them and says so. Close it, or use `fl_close_message`.

</details>

<details>
<summary><b>Piano roll: "FL Studio did not respond"</b></summary>

1. A piano roll must be open in FL Studio.
2. On macOS, run **Tools > Scripting > ComposeWithLLM** once per FL session from the *piano roll window's* menu (the ▸ arrow at its top left), not from FL's main Tools menu. On Windows the server does this itself; if a dialog was in the way, close it and retry.
3. Copy `ComposeWithLLM.pyscript` again after updating.
4. After updating the server, fully restart your MCP client: reconnecting may keep the old process running.

</details>

## Development

```bash
uv sync --python 3.12                    # runtime + dev dependencies
uv run pytest                            # tests; FL Studio not needed (its API is faked)
uv run pytest --cov --cov-report=term-missing
uv run ruff check .                      # lint
uv run python -c "from fl_studio_mcp.utils.connection import call; print(call('dev.reloadScript'))"
                                         # reload the controller after copying a new one in
```

The FL-side scripts run in FL's embedded Python and can't import the server package. The tests load them against fake FL modules (`tests/fakes.py`), and `tests/fake_browser.py` and `tests/fake_playlist.py` model the browser and the playlist. A contract test runs every controller tool against the real `dispatch_command`, and no test can reach a running FL. Changes that touch FL's UI still need a check in FL Studio.

**To add a controller command,** add a tool in `src/fl_studio_mcp/tools/<area>.py` that sends `"<area>.<verb>"`. Then add a matching `handle_<area>_<verb>` and a branch in `dispatch_command` in `fl_controller/device_FLStudioMCP.py`. `tests/test_tool_contract.py` catches any mismatch.

**Keep genre names out of the Python code:** genre knowledge belongs in style packs. A test enforces this.

<details>
<summary>Project structure</summary>

```text
fl-studio-mcp/
├── fl_controller/device_FLStudioMCP.py   # MIDI controller script (runs inside FL)
├── scripts/
│   ├── ComposeWithLLM.pyscript           # piano roll script (runs inside FL)
│   └── install_mcp_for_claude.*          # registers the server with Claude
├── src/fl_studio_mcp/
│   ├── server.py                         # FastMCP entry point
│   ├── tools/                            # the MCP tools, one module per area
│   ├── music/                            # theory, rhythm, transforms, arrangement, MIDI files
│   ├── audio/                            # WAV/ffmpeg decoding, loudness, pitch, tempo, melody,
│   │                                     #   mix diagnosis, reference compare, singing
│   ├── mixing/intents.json               # named mixing moves and chains
│   ├── project/                          # .flp / .fst parsing and preset building
│   ├── styles/*.json                     # style packs
│   └── utils/                            # connection, browser, playlist, mixer, render,
│                                         #   window messages, capture, paths
├── tests/                                # pytest suite with FL faked
├── install.ps1 / install.sh              # one-command installers
└── pyproject.toml
```

</details>

## Credits

- [karl-andres/fl-studio-mcp](https://github.com/karl-andres/fl-studio-mcp): the upstream project this fork is based on
- [calvinw/fl-studio-mcp](https://github.com/calvinw/fl-studio-mcp): the piano roll integration approach
- [rosasynthesiz/flstudio-mcp](https://github.com/rosasynthesiz/flstudio-mcp): ideas for mix diagnosis, snapshots and MIDI export
- [FL Studio API Stubs](https://github.com/IL-Group/FL-Studio-API-Stubs), [FastMCP](https://github.com/jlowin/fastmcp), [mido](https://github.com/mido/mido), [OpenUtau](https://github.com/stakira/OpenUtau), [Demucs](https://github.com/adefossez/demucs) (stem separation)
- [Image-Line](https://www.image-line.com/), for FL Studio

Not affiliated with Image-Line.

## License

MIT, see [LICENSE](LICENSE). The original copyright notice is kept.
