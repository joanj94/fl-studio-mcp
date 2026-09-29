"""MCP server module: connection tools and resources."""

from __future__ import annotations

import pytest

from fl_studio_mcp import server


class FakeConnection:
    def __init__(self, connected: bool, error: str | None = None, status: dict | None = None):
        self.is_connected = connected
        self.connection_error = error
        self._status = status or {}

    def ensure_connected(self) -> None:
        if not self.is_connected:
            raise RuntimeError(self.connection_error)

    def get_status(self) -> dict:
        return {"connected": self.is_connected, "error": self.connection_error, **self._status}

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        return {"success": True, "is_playing": True, "loop_mode": "song"}


@pytest.fixture
def use_connection(monkeypatch):
    def install(connection: FakeConnection) -> None:
        monkeypatch.setattr(server, "get_connection", lambda: connection)
        monkeypatch.setattr(server, "reset_connection", lambda: None)

    return install


def test_connect_success(use_connection):
    use_connection(FakeConnection(connected=True))

    assert "Successfully connected" in server.fl_connect()


def test_connect_failure_reports_reason(use_connection):
    use_connection(FakeConnection(connected=False, error="No virtual MIDI port found"))

    assert "No virtual MIDI port found" in server.fl_connect()


def test_connection_status_lists_ports(use_connection):
    use_connection(
        FakeConnection(connected=False, error="nope", status={"available_ports": ["Keyboard"]})
    )

    status = server.fl_connection_status()

    assert status["connected"] is False
    assert status["available_ports"] == ["Keyboard"]
    assert status["error"] == "nope"


def test_status_resource(use_connection):
    use_connection(FakeConnection(connected=False, error="down"))

    assert server.get_fl_status() == "Not connected: down"


def test_project_resource(use_connection):
    use_connection(FakeConnection(connected=True))

    project = server.get_project_info()

    assert project["is_playing"] is True
    assert project["loop_mode"] == "song"
