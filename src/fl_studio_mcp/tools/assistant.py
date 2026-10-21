"""Starting FL with its assistant reachable, FL's own tools, and raw access for what's left.

FL Studio 2026's built-in assistant ("Gopher") has 48 tools of its own, reached through
utils/gopher.py once FL runs with WebView2's debugging port (`fl_launch`). The
channel, effect and piano roll tools use them on their own when they can; these tools
expose the rest. `fl_api_call`/`fl_api_list` reach any function of FL's MIDI
scripting API through the controller, for what no tool covers yet.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fl_studio_mcp.tools.piano_roll import _select_target
from fl_studio_mcp.utils import gopher
from fl_studio_mcp.utils.connection import call

if TYPE_CHECKING:
    from fastmcp import FastMCP

# FL takes a while to start (plugins scanned, the template loaded).
LAUNCH_TIMEOUT = 120.0
POLL_INTERVAL = 0.5
WELCOME_CLASS = "TWelcomeWizard"
REGISTRY_PATHS = r"SOFTWARE\Image-Line\Shared\Paths"
HINT = ("Start FL Studio with fl_launch (it opens WebView2's debugging port for FL only), "
        "or run scripts/enable_fl_assistant_port.ps1 once so FL opens it however it starts. "
        "Without it the tools fall back to FL's menus and browser.")


def fl_program() -> str | None:
    """FL Studio's program: FL_MCP_FL_EXE, else the one FL's installer registered."""
    program = os.environ.get("FL_MCP_FL_EXE")
    if not program:
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_PATHS) as key:
                program = winreg.QueryValueEx(key, "FL Studio")[0]
        except (ImportError, OSError):
            return None
    return program if Path(program).is_file() else None


def _fl_running() -> bool:
    from fl_studio_mcp.utils import win_focus

    return win_focus.find_fl_window(*win_focus._libraries()) is not None


def _start(command: list[str], env: dict[str, str]) -> None:
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    subprocess.Popen(command, env=env, creationflags=flags, close_fds=True)


def _close_welcome() -> None:
    from fl_studio_mcp.utils import fl_windows

    fl_windows.close_windows(WELCOME_CLASS)


def _controller_answers(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if "error" not in call("transport.getStatus"):
            return True
        time.sleep(POLL_INTERVAL)
    return False


def _wait(condition, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)
    return True


def _open_assistant(timeout: float) -> str:
    """Close FL's welcome window and open the Gopher panel until its page answers.

    Seen live: FL creates its WebView2, and so opens the port, only with the panel's
    first page, and the welcome window (which comes a while after the main window)
    keeps the panel from opening. Raises gopher.GopherError.
    """
    deadline = time.monotonic() + timeout
    while True:
        _close_welcome()
        try:
            return gopher.open_panel(timeout=min(gopher.PANEL_TIMEOUT, timeout), create=True)
        except gopher.GopherError:
            if time.monotonic() >= deadline:
                raise


def register_assistant_tools(mcp: FastMCP) -> None:
    """Register FL launch, assistant and raw API tools with the MCP server."""

    @mcp.tool()
    def fl_launch(project: str | None = None) -> dict:
        """Start FL Studio so that its own assistant tools can be used (recommended).

        FL is started with WebView2's debugging port open for FL alone, its welcome
        window is closed and its Gopher panel opened once. Then channels and effects
        are added and deleted by FL's own tools (no browser walks or menus) and piano
        roll notes go through FL's own script runner (~10 times faster than the menu).
        A FL that is already running without the port has to be closed first (save
        the project); this never closes it. With the port policy set
        (scripts/enable_fl_assistant_port.ps1) any start of FL will do.

        Args:
            project: A .flp to open. Default: FL's start-up template.

        Returns {"launched", "assistant_tools"} or {"error"}. Windows only.
        """
        if project is not None and not Path(project).is_file():
            return {"error": f"There is no project file {project!r}"}
        launched = not _fl_running()
        if launched:
            program = fl_program()
            if not program:
                return {"error": "FL Studio's program wasn't found; set FL_MCP_FL_EXE to it"}
            env = {**os.environ, **gopher.launch_environment()}
            _start([program, *([project] if project else [])], env)
            if not _wait(_fl_running, LAUNCH_TIMEOUT):
                return {"error": f"FL Studio didn't start within {LAUNCH_TIMEOUT:g} s"}
        try:
            _open_assistant(LAUNCH_TIMEOUT if launched else gopher.PANEL_TIMEOUT)
            names = [t.get("name") for t in gopher.list_tools()]
        except gopher.PortClosed:
            return {"error": "FL Studio is running without the debugging port. Save the "
                             "project, close FL Studio, then call fl_launch to restart it "
                             "(or run scripts/enable_fl_assistant_port.ps1 once)."}
        except gopher.GopherError as e:
            return {"error": str(e)}
        result: dict[str, Any] = {"launched": launched, "assistant_tools": len(names)}
        if not _controller_answers(LAUNCH_TIMEOUT if launched else 2):
            result["warning"] = ("The MCP controller doesn't answer: enable 'FL Studio MCP "
                                 "Controller' in FL's MIDI settings.")
        return result

    @mcp.tool()
    def fl_assistant_status() -> dict:
        """Whether FL Studio's own assistant tools can be reached, and what they are.

        Returns {"reachable", "tools": [{"name", "description", "arguments"}]} or,
        when they can't be reached, {"reachable": false, "port_open", "hint"}.
        """
        if gopher.reachable() is None:
            return {"reachable": False, "port_open": gopher.port_open(), "port": gopher.port(),
                    "hint": HINT}
        try:
            catalog = gopher.list_tools()
        except gopher.GopherError as e:
            return {"error": str(e)}
        return {"reachable": True, "tools": [
            {"name": t.get("name"), "description": t.get("description", ""),
             "arguments": t.get("inputSchema", {}).get("properties", {})}
            for t in catalog
        ]}

    @mcp.tool()
    def fl_assistant_call(tool: str, arguments: dict | None = None) -> dict:
        """Run one of FL Studio's own assistant tools (fl_assistant_status lists them).

        Prefer this server's own tools, which check every change by reading back;
        these answer only with FL's text. Useful ones nothing else covers:
        reset_mixer_tracks, quantize_channel, show_highlight (points the user at a
        part of FL's window), get_session_context. Their channel and slot numbers
        count from 1, as FL shows them.

        Args:
            tool: The tool's name, e.g. "reset_mixer_tracks".
            arguments: Its arguments, e.g. {"target_tracks": "3,4"}.

        Returns {"tool", "result": FL's text} or {"error"}.
        """
        try:
            return {"tool": tool, "result": gopher.call_tool(tool, arguments or {})}
        except gopher.GopherError as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_run_piano_roll_python(code: str, returns: str | None = None,
                                 channel: int | str | None = None,
                                 pattern: int | None = None) -> dict:
        """Run Python in FL's piano roll, with the full `flpianoroll` API (needs fl_launch).

        For what the note tools don't cover: markers (`flp.score.addMarker`, key/scale
        and time signature markers), the user's selection (`Note.selected`,
        `score.getTimelineSelection()`), note groups, `score.clearNotes`. The code
        runs once at module level (no `apply`, no dialog) in the piano roll's
        channel and the current pattern; `import flpianoroll as flp` first. Files
        can't be written from there.

        Args:
            code: The Python to run.
            returns: A Python expression evaluated after the code; its value comes
                back as JSON. A run with `returns` changes nothing (FL discards it),
                so read in one call and change in another.
            channel: Channel index or role to show in the piano roll first.
            pattern: Pattern to make current first.

        Returns {"result"} or {"error": FL's traceback}.
        """
        target_error = _select_target(channel, pattern)
        if target_error:
            return {"error": target_error}
        try:
            return {"result": gopher.run_piano_roll_python(code, returns=returns)}
        except gopher.GopherError as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_api_call(module: str, function: str, args: list | None = None,
                    kwargs: dict | None = None) -> dict:
        """Call one function of FL's MIDI scripting API directly (for what no tool covers).

        E.g. fl_api_call("mixer", "getEqGain", [3, 0, 1]) reads mixer track 3's
        built-in EQ low band in dB. Modules: arrangement, channels, device, general,
        midi, mixer, patterns, playlist, plugins, transport, ui. Mind the API's
        pitfalls (pickupMode arguments, see the project notes); a change made this
        way isn't checked by reading back.

        Returns {"module", "function", "result"} or {"error"}.
        """
        return call("api.call", {"module": module, "function": function,
                                 "args": args or [], "kwargs": kwargs or {}})

    @mcp.tool()
    def fl_api_list(module: str, search: str = "") -> dict:
        """List the functions and constants of one module of FL's MIDI scripting API.

        Args:
            module: e.g. "mixer", "general", "midi" (constants such as REC_ ids).
            search: Text the names must contain (case ignored), e.g. "eq".

        Returns {"module", "functions", "constants"} or {"error"}.
        """
        return call("api.list", {"module": module, "search": search})
