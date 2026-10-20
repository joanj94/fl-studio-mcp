"""fl_launch, FL's own assistant tools, piano roll Python and raw API calls."""

from __future__ import annotations

import pytest

from fl_studio_mcp.tools import assistant
from fl_studio_mcp.utils import connection, gopher
from tests.fakes import ScriptedConnection, ToolCollector


@pytest.fixture
def tools() -> dict:
    collector = ToolCollector()
    assistant.register_assistant_tools(collector)
    return collector.tools


@pytest.fixture
def fl_state(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Whether FL runs, whether its port answers, and what was started."""
    state = {"running": False, "port": False, "started": [], "closed": [], "panel": 0}

    def start(command, env):
        state["started"].append((command, env))
        state["running"] = True
        state["port"] = True

    def open_panel(timeout=0):
        state["panel"] += 1
        return "ws://fake"

    monkeypatch.setattr(assistant, "_fl_running", lambda: state["running"])
    monkeypatch.setattr(assistant, "_start", start)
    monkeypatch.setattr(assistant, "_close_welcome", lambda: state["closed"].append(1))
    monkeypatch.setattr(assistant, "_controller_answers", lambda timeout: True)
    monkeypatch.setattr(assistant, "fl_program", lambda: "C:/FL/FL64.exe")
    monkeypatch.setattr(assistant, "LAUNCH_TIMEOUT", 0.2)
    monkeypatch.setattr(gopher, "port_open", lambda *a: state["port"])
    monkeypatch.setattr(gopher, "open_panel", open_panel)
    monkeypatch.setattr(gopher, "list_tools", lambda: [{"name": "add_channel"}, {"name": "play"}])
    return state


def test_launch_starts_fl_with_the_debugging_port(tools, fl_state):
    result = tools["fl_launch"]()

    assert result["launched"] is True
    command, env = fl_state["started"][0]
    assert command == ["C:/FL/FL64.exe"]
    assert env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"].startswith("--remote-debugging-port=")
    assert fl_state["closed"] and fl_state["panel"] == 1
    assert result["assistant_tools"] == 2


def test_launch_opens_a_project(tools, fl_state, tmp_path):
    project = tmp_path / "song.flp"
    project.write_bytes(b"FLhd")

    tools["fl_launch"](project=str(project))

    assert fl_state["started"][0][0] == ["C:/FL/FL64.exe", str(project)]


def test_launch_refuses_a_missing_project(tools, fl_state, tmp_path):
    assert "error" in tools["fl_launch"](project=str(tmp_path / "nope.flp"))
    assert fl_state["started"] == []


def test_launch_of_a_running_fl_with_the_port_only_opens_the_panel(tools, fl_state):
    fl_state.update(running=True, port=True)

    result = tools["fl_launch"]()

    assert result["launched"] is False
    assert fl_state["started"] == []
    assert fl_state["panel"] == 1


def test_launch_of_a_running_fl_without_the_port_says_to_restart(tools, fl_state):
    fl_state.update(running=True, port=False)

    result = tools["fl_launch"]()

    assert "restart" in result["error"].lower()
    assert fl_state["started"] == []


def test_status_without_the_port(tools, monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)
    monkeypatch.setattr(gopher, "port_open", lambda *a: False)

    assert tools["fl_assistant_status"]() == {"reachable": False, "port_open": False,
                                              "port": gopher.port(), "hint": assistant.HINT}


def test_status_lists_fls_tools(tools, monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: "ws://x")
    monkeypatch.setattr(gopher, "list_tools", lambda: [
        {"name": "play", "description": "Starts playback", "inputSchema": {}}])

    result = tools["fl_assistant_status"]()

    assert result["reachable"] is True
    assert result["tools"] == [{"name": "play", "description": "Starts playback",
                                "arguments": {}}]


def test_assistant_call_passes_through(tools, monkeypatch):
    seen = []
    monkeypatch.setattr(gopher, "call_tool", lambda name, args: seen.append((name, args)) or "ok")

    assert tools["fl_assistant_call"]("reset_mixer_tracks", {"target_tracks": "3"}) == {
        "tool": "reset_mixer_tracks", "result": "ok"}
    assert seen == [("reset_mixer_tracks", {"target_tracks": "3"})]


def test_assistant_call_error(tools, monkeypatch):
    def fail(name, args):
        raise gopher.GopherError("Error: bad")

    monkeypatch.setattr(gopher, "call_tool", fail)

    assert tools["fl_assistant_call"]("play", {}) == {"error": "Error: bad"}


def test_piano_roll_python_selects_the_target_then_runs(tools, monkeypatch):
    conn = ScriptedConnection()
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    runs = []
    monkeypatch.setattr(gopher, "run_piano_roll_python",
                        lambda code, returns=None: runs.append((code, returns)) or 7)

    result = tools["fl_run_piano_roll_python"]("x = 1", returns="x", channel=2, pattern=3)

    assert result == {"result": 7}
    assert [a for a, _ in conn.sent] == ["channels.showInPianoRoll", "patterns.select"]
    assert runs == [("x = 1", "x")]


def test_api_call_and_list_go_to_the_controller(tools, monkeypatch):
    conn = ScriptedConnection({"api.call": {"result": 3}, "api.list": {"functions": ["a"]}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)

    assert tools["fl_api_call"]("mixer", "trackCount")["result"] == 3
    assert tools["fl_api_list"]("mixer", search="eq")["functions"] == ["a"]
    assert conn.sent == [
        ("api.call", {"module": "mixer", "function": "trackCount", "args": [], "kwargs": {}}),
        ("api.list", {"module": "mixer", "search": "eq"}),
    ]
