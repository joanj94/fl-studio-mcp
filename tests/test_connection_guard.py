"""Commands that change FL are held back while FL shows a message box."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fl_studio_mcp.utils import connection


class Midi:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_command(self, action, params=None, timeout=2.0):
        self.sent.append(action)
        return {"success": True, "id": "x"}


@pytest.fixture
def fl(monkeypatch) -> Midi:
    midi = Midi()
    monkeypatch.setattr(connection, "get_midi_connection", lambda: midi)
    monkeypatch.setattr(connection.fl_windows, "open_message", lambda: "FL Studio trial")
    return midi


@pytest.mark.parametrize("action", ["channels.setName", "mixer.setTrackVolume", "project.undo",
                                    "browser.navigate", "ui.focusWindow"])
def test_a_change_is_not_sent_while_a_message_box_is_open(fl, action):
    result = connection.FLConnection().send_command(action, {})

    assert result["success"] is False
    assert "'FL Studio trial'" in result["error"] and "fl_close_message" in result["error"]
    assert fl.sent == []


@pytest.mark.parametrize("action", ["channels.getAll", "plugins.isValid", "plugins.listPresets",
                                    "patterns.findEmpty", "browser.getFocused"])
def test_reads_still_go_through(fl, action):
    assert connection.FLConnection().send_command(action, {})["success"] is True
    assert fl.sent == [action]


def test_everything_is_sent_when_no_message_box_is_open(fl, monkeypatch):
    monkeypatch.setattr(connection.fl_windows, "open_message", lambda: None)

    assert connection.FLConnection().send_command("channels.setName", {})["success"] is True


# Every controller command that only reads. A new command is a change unless it is
# added here, and a read must carry one of the read verbs to get past the guard.
READS = {
    "transport.getStatus", "transport.getLength", "mixer.getTrackCount", "mixer.getTrackInfo",
    "mixer.getAllTracks", "mixer.getTrackEffects", "channels.getCount", "channels.getInfo",
    "channels.getAll", "channels.getSelected", "channels.getGridBit",
    "channels.getStepSequence", "channels.getStepParams", "project.getTempo",
    "project.getInfo", "patterns.getAll", "patterns.findEmpty", "plugins.isValid",
    "plugins.getName", "plugins.getParamCount", "plugins.getParams", "plugins.getParamValue",
    "plugins.getPresetCount", "plugins.getColor", "plugins.listPresets", "browser.getFocused",
    "playlist.getState", "playlist.getSelectedTrack", "playlist.getTracks",
    "playlist.getSongPosition",
}


def test_the_guard_lets_through_exactly_the_controllers_reads(fl):
    source = (Path(__file__).parent.parent / "fl_controller" / "device_FLStudioMCP.py").read_text(
        encoding="utf-8")
    actions = set(re.findall(r'action == "([\w.]+)"', source))
    assert READS <= actions

    let_through = {a for a in actions if connection.blocked_by_message(a) is None}

    assert let_through == READS
