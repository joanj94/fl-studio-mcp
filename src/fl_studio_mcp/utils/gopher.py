"""FL Studio 2026's own assistant tools ("Gopher"), reached over WebView2's debugging port.

FL 2026 has a built-in MCP server behind its Gopher panel (Alt+F1). The panel is a
WebView2 page (gopher-fls.image-line.com) into which FL injects a host object,
`script_handler`: assigning a JSON-RPC `tools/call` to `script_handler.runJson` runs one
of FL's 48 tools, and FL answers through `window.flHelper.onRunJson` (there is no request
id, so one call at a time). Asking `script_handler.MCPTools = "1"` gets the catalog.

Its tools run in FL's own Python with a module `ilmcp` that MIDI scripts can't import
(seen live: not in their interpreter). It adds channels and effects by plugin name,
removes channels and effects, resets mixer tracks and runs piano roll scripts: all
without menus or confirm boxes (seen live, 0.5 s a call, the trial included).

To reach the page, FL must be started with WebView2's debugging port open
(`WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=9222`, see
`launch_environment`), or with WebView2's AdditionalBrowserArguments policy set for FL's
program (`scripts/enable_fl_assistant_port.ps1`), and the Gopher panel must have been
opened once in that FL session (`open_panel` posts Alt+F1). Seen live: FL creates its
WebView2, and with it the port, only when the panel first opens, so a closed port says
nothing until then. Nothing else is needed: no login, and the panel may stay hidden.

Piano roll scripts sent this way run at module level (seen live: `apply()` is never called,
module-level edits to `flpianoroll.score` stay) and can't write files (`open` fails inside
FL). A script's only way to answer is the text of an exception it raises, which also
discards its edits; `run_piano_roll_python(returns=...)` uses that for reads.

The WebSocket client is written by hand (the standard library has none) and does only
what the debugging protocol needs: text frames, masked as a client must.
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import re
import socket
import struct
import threading
import time
import urllib.request
from typing import Any
from urllib.parse import urlparse

DEFAULT_PORT = 9222
PAGE_HOST = "gopher-fls.image-line.com"
CALL_TIMEOUT = 20.0
PANEL_TIMEOUT = 15.0
# What the call script resolves to when FL doesn't answer in time.
NO_ANSWER = "__FL_MCP_NO_ANSWER__"
# A piano roll script's answer travels in the text of an exception between two markers.
RESULT_MARKER = "__FL_MCP_RESULT__"

_VK_F1 = 0x70
_WM_SYSKEYDOWN = 0x0104
_WM_SYSKEYUP = 0x0105
_ALT_DOWN = 1 << 29

POLICY_KEY = r"SOFTWARE\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments"

_lock = threading.Lock()
_tried_windows: set[int] = set()  # FL windows whose panel was opened for the policy
_RESULT = re.compile(re.escape(RESULT_MARKER) + r"([A-Za-z0-9+/=]+)" + re.escape(RESULT_MARKER))


class GopherError(Exception):
    """FL's assistant tools couldn't be reached, or a tool reported an error."""


class ToolError(GopherError):
    """FL ran the tool and answered with an error (as opposed to no or a lost answer)."""


class PortClosed(GopherError):
    """FL runs without WebView2's debugging port (not started by fl_launch, no policy)."""


def port() -> int:
    """The debugging port: FL_MCP_GOPHER_PORT, else 9222."""
    value = os.environ.get("FL_MCP_GOPHER_PORT", "")
    return int(value) if value.isdigit() else DEFAULT_PORT


def launch_environment() -> dict[str, str]:
    """Environment variables FL must be started with for the Gopher page to be reachable."""
    return {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port()}"}


def policy_set() -> bool:
    """True when WebView2's policy opens this port for an FL program (machine or user)."""
    try:
        import winreg
    except ImportError:
        return False
    wanted = f"--remote-debugging-port={port()}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, POLICY_KEY) as key:
                index = 0
                while True:
                    name, value, _kind = winreg.EnumValue(key, index)
                    index += 1
                    if name.upper().startswith("FL") and wanted in str(value):
                        return True
        except OSError:  # no key, or no more values
            continue
    return False


# --- WebSocket, as little as the debugging protocol needs -----------------------------


def _send_text(sock: Any, text: str) -> None:
    data = text.encode("utf-8")
    size = len(data)
    if size < 126:
        head = bytes([0x81, 0x80 | size])
    elif size < 65536:
        head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", size)
    else:
        head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", size)
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    sock.sendall(head + mask + masked)


def _read_exactly(sock: Any, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise GopherError("The connection to FL's Gopher page was closed")
        data += chunk
    return data


def _receive_text(sock: Any) -> str:
    """One whole text message; control frames (ping, pong) are skipped."""
    message = b""
    while True:
        first, second = _read_exactly(sock, 2)
        size = second & 0x7F
        if size == 126:
            size = struct.unpack(">H", _read_exactly(sock, 2))[0]
        elif size == 127:
            size = struct.unpack(">Q", _read_exactly(sock, 8))[0]
        payload = _read_exactly(sock, size)
        opcode = first & 0x0F
        if opcode >= 8:
            if opcode == 8:
                raise GopherError("FL's Gopher page closed the connection")
            continue
        message += payload
        if first & 0x80:
            return message.decode("utf-8")


def _connect(url: str, timeout: float) -> socket.socket:
    parsed = urlparse(url)
    sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(
        f"GET {parsed.path} HTTP/1.1\r\nHost: {parsed.hostname}:{parsed.port}\r\n"
        "Upgrade: websocket\r\nConnection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n".encode()
    )
    answer = b""
    while b"\r\n\r\n" not in answer:
        chunk = sock.recv(4096)
        if not chunk:
            break
        answer += chunk
    if b" 101 " not in answer.split(b"\r\n", 1)[0]:
        sock.close()
        raise GopherError(f"FL's Gopher page refused the connection: {answer[:120]!r}")
    return sock


# --- The page --------------------------------------------------------------------------


# Seen live while FL was starting: a poll of the closed port got its own request back
# as the answer (a loopback connection to itself), which http.client raises as
# BadStatusLine, not OSError.
UNREADABLE = (OSError, ValueError, http.client.HTTPException)


def _targets(debug_port: int) -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{debug_port}/json/list", timeout=2) as response:
        return json.loads(response.read().decode("utf-8"))


def page_url(debug_port: int | None = None) -> str | None:
    """The Gopher page's debugging WebSocket, or None (port closed, panel never opened)."""
    try:
        targets = _targets(debug_port or port())
    except UNREADABLE:
        return None
    for target in targets:
        url = urlparse(target.get("url", ""))
        if target.get("type") == "page" and url.scheme == "https" and url.hostname == PAGE_HOST:
            return target.get("webSocketDebuggerUrl")
    return None


def port_open(debug_port: int | None = None) -> bool:
    try:
        _targets(debug_port or port())
        return True
    except UNREADABLE:
        return False


def _fl_window() -> tuple[Any, int]:
    from fl_studio_mcp.utils import win_focus

    user32, kernel32 = win_focus._libraries()
    main = win_focus.find_fl_window(user32, kernel32)
    if main is None:
        raise GopherError("FL Studio is not running")
    return user32, main


def open_panel(timeout: float = PANEL_TIMEOUT, create: bool = False) -> str:
    """Open FL's Gopher panel (posted Alt+F1) and wait for its page; returns its WebSocket.

    With the port closed this fails at once, unless `create`: FL opens the port with
    the panel's first page, so the panel is opened and the port waited for.
    Raises PortClosed when FL has no debugging port, GopherError when FL isn't running.
    """
    url = page_url()
    if url:
        return url
    user32, main = _fl_window()
    closed = PortClosed(
        "FL Studio's assistant page can't be reached: FL must be started with "
        f"{launch_environment()} (fl_launch does that), or run "
        "scripts/enable_fl_assistant_port.ps1 once."
    )
    if not create and not port_open():
        raise closed
    user32.PostMessageW(main, _WM_SYSKEYDOWN, _VK_F1, _ALT_DOWN | 1)
    user32.PostMessageW(main, _WM_SYSKEYUP, _VK_F1, _ALT_DOWN | (3 << 30) | 1)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        url = page_url()
        if url:
            return url
        time.sleep(0.25)
    if not port_open():
        raise closed
    raise GopherError("FL's Gopher panel didn't open (Alt+F1) within %g s" % timeout)


def reachable() -> str | None:
    """The page's WebSocket when FL's tools can be used, else None.

    With the policy set, FL's port only opens with the panel, so the panel is opened
    once per FL window (never again for that window, also when it failed).
    """
    url = page_url()
    if url or port_open() or not policy_set():
        return url
    try:
        _user32, main = _fl_window()
        if main in _tried_windows:
            return None
        _tried_windows.add(main)
        return open_panel(create=True)
    except GopherError:
        return None


def evaluate(script: str, timeout: float) -> Any:
    """Run JavaScript in the Gopher page and return its (awaited) value."""
    try:
        sock = _connect(open_panel(), timeout + 5)
        try:
            _send_text(sock, json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": script, "awaitPromise": True, "returnByValue": True},
            }))
            while True:
                message = json.loads(_receive_text(sock))
                if message.get("id") == 1:
                    break
        finally:
            sock.close()
    except (OSError, ValueError) as e:  # socket timeouts, broken JSON or text
        raise GopherError(f"The connection to FL's Gopher page failed: {e!r}") from e
    result = message.get("result", {})
    if "exceptionDetails" in result or "error" in message:
        details = result.get("exceptionDetails") or message.get("error")
        raise GopherError(f"The Gopher page raised: {json.dumps(details)[:300]}")
    return result.get("result", {}).get("value")


def _script(assignment: str, callback: str, timeout: float) -> str:
    """JavaScript that hands FL one request and resolves to FL's answer (or NO_ANSWER)."""
    return f"""new Promise(function (resolve) {{
  var helper = window.flHelper || (window.flHelper = {{}});
  var previous = helper.{callback};
  var finished = false, timer = null;
  var done = function (value) {{
    if (finished) return;
    finished = true;
    clearTimeout(timer);
    if (helper.{callback} === done) helper.{callback} = previous;
    resolve(value);
  }};
  helper.{callback} = done;
  timer = setTimeout(function () {{ done({json.dumps(NO_ANSWER)}); }}, {int(timeout * 1000)});
  var host = (typeof script_handler === 'object' && script_handler)
    || window.chrome.webview.hostObjects.script_handler;
  {assignment}
}})"""


def call_tool(name: str, arguments: dict[str, Any], timeout: float = CALL_TIMEOUT) -> str:
    """Run one of FL's own assistant tools; returns its text, raises GopherError on errors."""
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments}})
    assignment = f"var request = {json.dumps(request)};\n  host.runJson = request;"
    with _lock:
        answer = evaluate(_script(assignment, "onRunJson", timeout), timeout)
    if answer == NO_ANSWER or answer is None:
        raise GopherError(f"FL did not answer {name} within {timeout:g} s")
    text, is_error = _read_answer(answer)
    if text is None:
        raise GopherError(f"FL's answer to {name} couldn't be read: {answer[:200]!r}")
    if is_error:
        raise ToolError(text)
    return text


# FL's answer when it isn't valid JSON: seen live, quotes inside the text aren't escaped.
_LOOSE_ANSWER = re.compile(
    r'"text":\s*"(?P<text>.*)"\s*}\s*\]\s*,\s*"isError":\s*(?P<error>true|false)', re.DOTALL)


def _read_answer(answer: str) -> tuple[str | None, bool]:
    """(text, is_error) of FL's JSON-RPC answer; text is None if it can't be read."""
    try:
        result = json.loads(answer)["result"]
        text = "\n".join(c.get("text", "") for c in result.get("content", []))
        return text, bool(result.get("isError"))
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    match = _LOOSE_ANSWER.search(answer)
    if match is None:
        return None, False
    raw = match.group("text")
    try:  # escape the bare quotes, then let JSON undo FL's other escapes
        text = json.loads('"' + re.sub(r'(?<!\\)"', r'\\"', raw) + '"')
    except ValueError:
        text = raw.replace("\\n", "\n").replace("\\t", "\t")
    return text, match.group("error") == "true"


def list_tools(timeout: float = CALL_TIMEOUT) -> list[dict]:
    """FL's catalog of assistant tools (name, description, inputSchema)."""
    with _lock:
        answer = evaluate(_script('host.MCPTools = "1";', "onMCPTools", timeout), timeout)
    if answer == NO_ANSWER or answer is None:
        raise GopherError(f"FL did not send its tool list within {timeout:g} s")
    return json.loads(answer)


def run_piano_roll_python(source: str, returns: str | None = None,
                          timeout: float = CALL_TIMEOUT) -> Any:
    """Run Python inside FL's piano roll (the channel it shows, the current pattern).

    The code runs at module level with `flpianoroll` importable. With `returns` (a Python
    expression, evaluated after the code), the value comes back as JSON; that run's edits
    are discarded (it answers by raising), so use it for reads. Without it, edits stay.
    """
    if returns is not None:
        # Base64, because FL doesn't escape quotes in the text it answers with.
        source = (f"{source}\nimport json as _json, binascii as _binascii\n"
                  f"raise Exception({RESULT_MARKER!r} + _binascii.b2a_base64("
                  f"_json.dumps({returns}).encode(), newline=False).decode() "
                  f"+ {RESULT_MARKER!r})\n")
    try:
        text = call_tool("run_piano_roll_script", {"source": source}, timeout)
    except ToolError as e:  # a script that raised; anything else (no answer) propagates
        text = str(e)
    found = _RESULT.search(text)
    if found:
        try:
            return json.loads(base64.b64decode(found.group(1)))
        except ValueError as e:
            raise GopherError(f"The piano roll script's result couldn't be read: {e}") from e
    if "Traceback" in text:
        raise ToolError(text.strip())
    if returns is not None:
        raise GopherError(f"The piano roll script gave no result: {text.strip()[:300]}")
    return None
