"""Pictures of FL Studio's window: the PNG encoding, cropping and the fl_screenshot tool."""

from __future__ import annotations

import struct
import zlib
from typing import Any

import pytest

from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools import register_screen_tools, screen
from fl_studio_mcp.utils import connection, fl_capture, fl_playlist
from tests.fakes import ScriptedConnection, ToolCollector

RED, GREEN, BLUE = b"\x00\x00\xff\x00", b"\x00\xff\x00\x00", b"\xff\x00\x00\x00"  # as BGRX


def _decode(png: bytes) -> tuple[int, int, list[bytes]]:
    """Width, height and the RGB rows of a PNG made by encode_png."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    chunks, at = {}, 8
    while at < len(png):
        (length,), kind = struct.unpack(">I", png[at:at + 4]), png[at + 4:at + 8]
        data = png[at + 8:at + 8 + length]
        assert struct.unpack(">I", png[at + 8 + length:at + 12 + length])[0] == zlib.crc32(
            kind + data)
        chunks[kind] = data
        at += 12 + length
    width, height, depth, colour = struct.unpack(">IIBB", chunks[b"IHDR"][:10])
    assert (depth, colour) == (8, 2)
    raw = zlib.decompress(chunks[b"IDAT"])
    row = 1 + width * 3
    assert all(raw[start] == 0 for start in range(0, len(raw), row))
    return width, height, [raw[start + 1:start + row] for start in range(0, len(raw), row)]


def test_png_holds_the_pixels_in_rgb_order():
    width, height, rows = _decode(fl_capture.encode_png(3, 2, (RED + GREEN + BLUE) * 2))

    assert (width, height) == (3, 2)
    assert rows == [b"\xff\x00\x00" + b"\x00\xff\x00" + b"\x00\x00\xff"] * 2


def test_png_refuses_pixels_that_do_not_fill_the_picture():
    with pytest.raises(ValueError):
        fl_capture.encode_png(2, 2, RED * 3)


def test_crop_cuts_an_area_and_keeps_inside_the_picture():
    picture = RED + GREEN + BLUE + BLUE + GREEN + RED  # 3 wide, 2 high

    assert fl_capture.crop(3, picture, (1, 0, 3, 1)) == (2, 1, GREEN + BLUE)
    assert fl_capture.crop(3, picture, (2, 1, 9, 9)) == (1, 1, RED)
    with pytest.raises(ValueError):
        fl_capture.crop(3, picture, (5, 0, 9, 2))


@pytest.fixture
def tools(monkeypatch, tmp_path) -> dict[str, Any]:
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(tmp_path))
    collector = ToolCollector()
    register_screen_tools(collector)
    return collector.tools


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection()
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    monkeypatch.setattr(screen, "FRONT_DELAY", 0.0)
    return conn


def test_the_tool_returns_the_picture_of_fls_window_as_it_is(tools, fl, monkeypatch):
    asked = []
    monkeypatch.setattr(fl_capture, "capture", lambda panel: asked.append(panel) or (4, 2, b"png"))

    picture = tools["fl_screenshot"]()

    assert asked == ["all"] and fl.sent == []
    assert picture.data == b"png"


def test_a_panel_is_brought_to_the_front_before_its_picture(tools, fl, monkeypatch):
    monkeypatch.setattr(fl_capture, "capture", lambda panel: (4, 2, b"png"))

    tools["fl_screenshot"](" Piano Roll ")

    assert fl.sent == [("ui.focusWindow", {"window": "piano roll"})]


def test_the_playlist_is_zoomed_out_to_the_whole_song(tools, fl, monkeypatch):
    shown = []
    monkeypatch.setattr(fl_playlist, "show_whole_song", lambda: shown.append(True))
    monkeypatch.setattr(fl_capture, "capture", lambda panel: (4, 2, b"png"))

    tools["fl_screenshot"]("playlist")

    assert shown == [True]


def test_a_panel_that_cannot_be_shown_is_an_error(tools, fl, monkeypatch):
    fl.results["ui.focusWindow"] = {"error": "FL said no"}

    assert tools["fl_screenshot"]("mixer") == {"error": "FL said no"}


def test_the_tool_can_save_the_picture_instead(tools, fl, monkeypatch, tmp_path):
    monkeypatch.setattr(fl_capture, "capture", lambda panel: (4, 2, b"png"))

    result = tools["fl_screenshot"]("piano roll", save=True)

    assert (result["width"], result["height"]) == (4, 2)
    assert result["path"].startswith(str(tmp_path)) and "fl-piano-roll-" in result["path"]
    assert open(result["path"], "rb").read() == b"png"


def test_the_tool_reports_why_no_picture_was_taken(tools, monkeypatch):
    monkeypatch.setattr(fl_capture.platform, "system", lambda: "Windows")

    assert "must be 'all' or one of" in tools["fl_screenshot"]("toolbar")["error"]
    # Reaching Windows is refused in tests: that must come back as an error too.
    assert "could not be taken" in tools["fl_screenshot"]()["error"]


def test_screenshots_are_windows_only(tools, monkeypatch):
    monkeypatch.setattr(fl_capture.platform, "system", lambda: "Linux")

    assert "only supported on Windows" in tools["fl_screenshot"]()["error"]


def test_a_picture_that_cannot_be_saved_is_an_error(tools, fl, monkeypatch, tmp_path):
    monkeypatch.setattr(fl_capture, "capture", lambda panel: (4, 2, b"png"))
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("")
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(blocked))

    assert "could not be saved" in tools["fl_screenshot"](save=True)["error"]
