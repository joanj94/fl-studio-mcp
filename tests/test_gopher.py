"""FL's built-in assistant tools over WebView2's debugging port (utils/gopher.py)."""

from __future__ import annotations

import base64
import json
import struct

import pytest

from fl_studio_mcp.utils import gopher


class FakeSocket:
    """Serves prepared bytes to recv() and records what was sent."""

    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming = incoming
        self.sent = b""

    def recv(self, size: int) -> bytes:
        chunk, self.incoming = self.incoming[:size], self.incoming[size:]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def close(self) -> None:
        pass


def server_frame(text: str, fin: bool = True, opcode: int = 1) -> bytes:
    data = text.encode()
    head = bytes([(0x80 if fin else 0) | opcode])
    if len(data) < 126:
        return head + bytes([len(data)]) + data
    if len(data) < 65536:
        return head + bytes([126]) + struct.pack(">H", len(data)) + data
    return head + bytes([127]) + struct.pack(">Q", len(data)) + data


def unmask(frame: bytes) -> str:
    length = frame[1] & 0x7F
    offset = 2
    if length == 126:
        length = struct.unpack(">H", frame[2:4])[0]
        offset = 4
    elif length == 127:
        length = struct.unpack(">Q", frame[2:10])[0]
        offset = 10
    mask, payload = frame[offset:offset + 4], frame[offset + 4:offset + 4 + length]
    return bytes(b ^ mask[i % 4] for i, b in enumerate(payload)).decode()


def sent_request(script: str) -> dict:
    """The JSON-RPC request a call script hands to FL."""
    literal = script.split("var request = ", 1)[1]
    text, _ = json.JSONDecoder().raw_decode(literal)
    return json.loads(text)


@pytest.mark.parametrize("size", [5, 300, 70000])
def test_sent_frames_are_masked_text_of_any_length(size):
    sock = FakeSocket()
    text = "x" * size

    gopher._send_text(sock, text)

    assert sock.sent[0] == 0x81
    assert sock.sent[1] & 0x80  # masked, as a client must
    assert unmask(sock.sent) == text


@pytest.mark.parametrize("size", [5, 300, 70000])
def test_received_messages_of_any_length(size):
    assert gopher._receive_text(FakeSocket(server_frame("y" * size))) == "y" * size


def test_received_message_split_over_frames_and_pings_skipped():
    incoming = server_frame("", opcode=9) + server_frame("ab", fin=False) + server_frame(
        "cd", opcode=0)

    assert gopher._receive_text(FakeSocket(incoming)) == "abcd"


def test_closed_connection_is_an_error():
    with pytest.raises(gopher.GopherError):
        gopher._receive_text(FakeSocket(b"\x81"))


def test_page_is_the_gopher_target(monkeypatch):
    targets = [
        {"type": "page", "url": "https://example.com/", "webSocketDebuggerUrl": "ws://a"},
        {"type": "page", "url": "https://gopher-fls.image-line.com/?x=1",
         "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/1"},
    ]
    monkeypatch.setattr(gopher, "_targets", lambda port: targets)

    assert gopher.page_url(9222) == "ws://127.0.0.1:9222/devtools/page/1"


def test_no_gopher_page_is_none(monkeypatch):
    monkeypatch.setattr(gopher, "_targets", lambda port: [])
    assert gopher.page_url(9222) is None


def test_closed_port_is_none(monkeypatch):
    def refuse(port):
        raise OSError("refused")

    monkeypatch.setattr(gopher, "_targets", refuse)
    assert gopher.page_url(9222) is None


def test_a_garbled_answer_while_fl_starts_is_a_closed_port(monkeypatch):
    def echo(port):  # seen live: the poll got its own request back
        import http.client

        raise http.client.BadStatusLine("GET /json/list HTTP/1.1")

    monkeypatch.setattr(gopher, "_targets", echo)
    assert gopher.port_open(9222) is False
    assert gopher.page_url(9222) is None


def reply(text: str, is_error: bool = False) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": "1", "result": {
        "content": [{"type": "text", "text": text}], "isError": is_error}})


@pytest.fixture
def evaluated(monkeypatch):
    """Records the JavaScript evaluated and answers with `answers` in turn."""
    seen: dict = {"scripts": [], "answers": []}

    def evaluate(script, timeout):
        seen["scripts"].append(script)
        return seen["answers"].pop(0)

    monkeypatch.setattr(gopher, "evaluate", evaluate)
    return seen


def test_call_tool_returns_the_tools_text(evaluated):
    evaluated["answers"] = [reply("Successfully added 3x Osc to the Channel Rack.")]

    text = gopher.call_tool("add_channel", {"plugin": "3x Osc"})

    assert text == "Successfully added 3x Osc to the Channel Rack."
    request = sent_request(evaluated["scripts"][0])
    assert request["method"] == "tools/call"
    assert request["params"] == {"name": "add_channel", "arguments": {"plugin": "3x Osc"}}


def test_call_tool_error_is_raised(evaluated):
    evaluated["answers"] = [reply("Error: No valid tracks", is_error=True)]

    with pytest.raises(gopher.GopherError, match="No valid tracks"):
        gopher.call_tool("add_effect", {})


def test_call_tool_without_an_answer_is_an_error(evaluated):
    evaluated["answers"] = [gopher.NO_ANSWER]

    with pytest.raises(gopher.GopherError, match="did not answer"):
        gopher.call_tool("play", {})


def test_list_tools_parses_the_catalog(evaluated):
    evaluated["answers"] = [json.dumps([{"name": "play", "inputSchema": {}}])]

    assert gopher.list_tools() == [{"name": "play", "inputSchema": {}}]
    assert "MCPTools" in evaluated["scripts"][0]


def test_unescaped_quotes_in_fls_answer_are_tolerated(evaluated):
    # Seen live: FL escapes newlines in a tool's text but not quotes.
    evaluated["answers"] = [
        '{ \n  "jsonrpc": "2.0",\n  "id": "1",\n  "result": {\n    "content": [\n      {\n'
        '        "type": "text",\n        "text": "{\\n  "tempo_bpm": 130.0\\n}"\n'
        '      }\n    ],\n    "isError": false\n  }\n}'
    ]

    assert gopher.call_tool("get_session_context", {}) == '{\n  "tempo_bpm": 130.0\n}'


def test_unescaped_answer_that_is_an_error_is_raised(evaluated):
    evaluated["answers"] = [
        '{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "No "x" here"}],'
        ' "isError": true}}'
    ]

    with pytest.raises(gopher.GopherError, match='No "x" here'):
        gopher.call_tool("remove_channels", {})


def test_python_result_is_read_from_the_raised_marker(evaluated):
    marker = gopher.RESULT_MARKER
    encoded = base64.b64encode(json.dumps({"count": 3}).encode()).decode()
    evaluated["answers"] = [reply(
        "Traceback (most recent call last):\n<module> (<string> line 9)\n"
        f"Exception ({marker}{encoded}{marker})\n\n", is_error=False)]

    assert gopher.run_piano_roll_python("x = 1", returns="{'count': 3}") == {"count": 3}
    source = sent_request(evaluated["scripts"][0])["params"]["arguments"]["source"]
    assert source.startswith("x = 1")
    assert marker in source


def test_python_error_without_the_marker_is_raised(evaluated):
    evaluated["answers"] = [reply("Traceback ...\nNameError (name 'y')\n")]

    with pytest.raises(gopher.GopherError, match="NameError"):
        gopher.run_piano_roll_python("y", returns="1")


def test_python_without_a_result_runs_as_given(evaluated):
    evaluated["answers"] = [reply("Script executed without errors")]

    assert gopher.run_piano_roll_python("x = 1") is None
    assert sent_request(evaluated["scripts"][0])["params"]["arguments"]["source"] == "x = 1"


def test_python_without_a_result_that_fails_is_raised(evaluated):
    evaluated["answers"] = [reply("Traceback ...\nNameError (name 'y')\n")]

    with pytest.raises(gopher.GopherError, match="NameError"):
        gopher.run_piano_roll_python("y")


def test_python_whose_answer_is_lost_raises_instead_of_succeeding(evaluated):
    evaluated["answers"] = [gopher.NO_ANSWER]

    with pytest.raises(gopher.GopherError, match="did not answer") as raised:
        gopher.run_piano_roll_python("x = 1")
    assert not isinstance(raised.value, gopher.ToolError)


def test_a_script_error_is_a_tool_error(evaluated):
    evaluated["answers"] = [reply("Traceback ...\nNameError (name 'y')\n", is_error=True)]

    with pytest.raises(gopher.ToolError):
        gopher.run_piano_roll_python("y")


def test_loose_answers_undo_fls_other_escapes(evaluated):
    # FL's text: a, quoted q, an escaped backslash and an escaped e-acute.
    evaluated["answers"] = [
        r'{"result": {"content": [{"type": "text", "text": "a "q" \\ b\u00e9"}],'
        ' "isError": false}}'
    ]

    assert gopher.call_tool("x", {}) == 'a "q" \\ b\u00e9'


@pytest.fixture
def fl_window(monkeypatch):
    """FL's main window and the Alt+F1 posted to it."""
    posted = []

    class User32:
        def PostMessageW(self, *args):  # noqa: N802 - Windows' name
            posted.append(args)

    monkeypatch.setattr(gopher, "_fl_window", lambda: (User32(), 42))
    monkeypatch.setattr(gopher, "_tried_windows", set())
    return posted


def test_with_the_policy_the_panel_is_opened_once_per_fl_window(fl_window, monkeypatch):
    opened = []
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)
    monkeypatch.setattr(gopher, "port_open", lambda *a: False)
    monkeypatch.setattr(gopher, "policy_set", lambda: True)
    monkeypatch.setattr(gopher, "open_panel",
                        lambda timeout=0, create=False: opened.append(create) or "ws://page")

    assert gopher.reachable() == "ws://page"
    assert gopher.reachable() is None  # not again for the same window
    assert opened == [True]


def test_without_the_policy_a_closed_port_is_left_alone(fl_window, monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)
    monkeypatch.setattr(gopher, "port_open", lambda *a: False)
    monkeypatch.setattr(gopher, "policy_set", lambda: False)

    assert gopher.reachable() is None
    assert fl_window == []


def test_open_panel_with_a_closed_port_fails_at_once_unless_creating(fl_window, monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)
    monkeypatch.setattr(gopher, "port_open", lambda *a: False)

    with pytest.raises(gopher.PortClosed):
        gopher.open_panel()
    assert fl_window == []
    with pytest.raises(gopher.PortClosed):
        gopher.open_panel(timeout=0.01, create=True)
    assert len(fl_window) == 2  # Alt+F1 down and up were posted
