# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                      # install deps (Windows: uv sync --python 3.12 — python-rtmidi only ships 3.12 wheels there)
uv run fl-studio-mcp         # run the MCP server (stdio)
uv run ruff check .          # lint (rules E,F,I,W; line length 100)
uv run pytest                # all tests
uv run pytest tests/test_controller.py::test_response_echoes_request_id   # single test
uv run pytest --cov --cov-report=term-missing                           # coverage
```

Environment variables: `FL_MCP_MIDI_PORT` (MIDI output port name or substring) and `FL_MCP_SETTINGS_DIR` (FL `Settings` folder, for a custom FL user data folder; server and `install.ps1` only — the FL-side scripts can't see it).

`install.ps1` / `install.sh` do the full setup: uv, deps, copying the FL-side scripts into FL Studio's Settings folder, and registering the server with Claude (`scripts/install_mcp_for_claude.*`).

Fork of https://github.com/karl-andres/fl-studio-mcp: `origin` is the fork, `upstream` is the original repo.

## Architecture

The code runs in two separate Python runtimes that share nothing except JSON files on disk:

1. **MCP server** (`src/fl_studio_mcp/`): a normal Python process built on FastMCP. `server.py` creates the `mcp` instance, and each `tools/*.py` module exposes `register_*_tools(mcp)`, which defines `@mcp.tool()` functions as closures.
2. **FL-side scripts**: these run *inside FL Studio's embedded Python*. They import FL's API modules (`channels`, `mixer`, `plugins`, `transport`, `flpianoroll`), so they can't be imported or run outside FL. The `fl-studio-api-stubs` dev dependency only gives type hints. Editing them in the repo has no effect until they are copied into FL's Settings folder (re-run the installer or copy by hand) and FL reloads the script.

### Channel 1: MIDI controller (transport, mixer, channels, plugins)

`tools/*.py` → `utils/connection.py` (a thin singleton wrapper) → `utils/midi_connection.py`:
- atomically writes `{"id": <uuid>, "action": "mixer.setTrackVolume", "params": {...}}` to `Settings/Hardware/FLStudioMCP/mcp_command.json`. A lock keeps one command in flight at a time.
- sends MIDI note_on 127 over a virtual port. `select_output_port` uses `FL_MCP_MIDI_PORT` if set, otherwise a port whose name contains `IAC`, `loopMIDI` or `FL`. It never falls back to an arbitrary (possibly hardware) port.
- polls `mcp_response.json` (20ms interval, 2s timeout) and only accepts a response whose `id` matches. Stale responses are ignored.

`fl_controller/device_FLStudioMCP.py`: `OnMidiMsg` detects note 127, then `dispatch_command()` (a long if/elif chain on the action string) calls a `handle_<area>_<verb>(params)` function. That function returns a dict, which gets merged into `{"success": ..., "id": ...}`. `success` is False if the dict has an `"error"` key or the handler raised. A response is always written (atomically), even if the result isn't JSON-serializable.

**To add a command**, change both sides: add a tool in `tools/<area>.py` that calls `conn.send_command("<area>.<verb>", {...})`, and add a branch in `dispatch_command` plus a `handle_*` function in the controller. The action strings must match exactly; `tests/test_tool_contract.py` catches mismatches automatically. Tools check errors with `if not result.get("success", False) and "error" in result`.

### Channel 2: Piano roll script (persistent notes)

`tools/piano_roll.py` appends request dicts (`add_notes`, `add_chord`, `delete_notes`, `clear`), each tagged with an `id`, to a JSON list in `Settings/Piano roll scripts/mcp_request.json`. `utils/fl_trigger.py` then brings the FL window to the front (on Windows via ctypes `user32`, plus an ALT tap to allow `SetForegroundWindow`) and sends `Ctrl+Alt+Y` (`Cmd+Opt+Y` on macOS). The tool then waits (up to `RESPONSE_TIMEOUT`, 5s) for `mcp_response.json` to list all its ids in `request_ids`.

`scripts/ComposeWithLLM.pyscript` processes the queue, resets it to `[]`, exports all notes to `piano_roll_state.json` (read by `fl_get_piano_roll_state`), and always writes a response (`status: success|error|idle`, `request_ids`, `notes_added`, `notes_deleted`). Times and durations are in quarter notes on the server side and are converted with `flp.score.PPQ` inside FL. The user has to run the script manually once per session (Tools > Scripting) before the keystroke trigger works.

### Paths

The server resolves `<Documents>/Image-Line/FL Studio/Settings` in `utils/paths.py`. On Windows it asks the shell for the real Documents folder, so OneDrive redirection works. The FL-side scripts can't import that module, so each carries its own copy of the lookup, and `install.ps1` uses `[Environment]::GetFolderPath("MyDocuments")`. If you change where files are read or written, update all of these together. Shared JSON files are always written through temp-file + `os.replace` (`atomic_write_json` / `write_json_atomic`).

### Tests

`tests/fakes.py` provides fake FL modules (`MagicMock`s for the controller, a list-backed `flpianoroll`) and `ToolCollector` (a stand-in for FastMCP that records `@mcp.tool()` functions). `tests/conftest.py` loads the FL-side scripts from source with those fakes and redirects their JSON files to `tmp_path`. The `settings_dir` fixture points the server at a temporary folder via `FL_MCP_SETTINGS_DIR`. `test_tool_contract.py` calls every MIDI-channel tool against the real `dispatch_command` in-process.

## FL API gotchas (already fixed once; don't regress)

- `plugins.setParamValue(value, paramIndex, index, slotIndex, pickupMode, useGlobalIndex)`: the 5th argument is **pickupMode**. Passing `True` there makes FL silently ignore the value.
- Colours go to FL's API as `0xRRGGBB`. Tools take `red, green, blue` ints and the controller packs them.
- The scripting API cannot load plugins or create patterns. Don't try to add tools for either.
- `fl_trigger_note` is real-time only (notes aren't stored unless FL is recording). Use the step sequencer or piano roll to place notes permanently.
