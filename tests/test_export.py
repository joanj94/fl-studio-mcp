"""Exporting a song: measured on a WAV, kept as an MP3 (tools/export.py)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools import export
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector
from tests.test_audio import write_wav

RATE = 44100


@pytest.fixture
def fl(monkeypatch, tmp_path):
    conn = ScriptedConnection({"transport.getStatus": {"loop_mode": "pattern"}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(tmp_path / "renders"))
    (tmp_path / "renders").mkdir()
    rendered: list[Path] = []

    def render(path: Path, timeout: float) -> float:
        assert not path.exists()
        tone = 0.5 * np.sin(2 * np.pi * 220 * np.arange(RATE * 3) / RATE)
        if path.suffix == ".wav":
            write_wav(path, np.column_stack([tone, tone]), RATE, 3, 32)
        else:
            path.write_bytes(b"ID3")
        rendered.append(path)
        return 1.0

    monkeypatch.setattr(export, "render_audio", render)
    collector = ToolCollector()
    export.register_export_tools(collector)
    return collector.tools["fl_export_song"], conn, rendered


def test_song_names_become_dated_slugs(tmp_path):
    when = datetime(2026, 10, 21, 21, 5)
    assert export.song_path(tmp_path, "Night Drive 185bpm F#m!", when).name == (
        "night-drive-185bpm-f#m-20261021-2105.mp3")
    with pytest.raises(ValueError):
        export.song_path(tmp_path, " ?! ", when)


def test_the_song_is_measured_on_a_wav_and_kept_as_an_mp3(fl, tmp_path):
    tool, conn, rendered = fl
    music = tmp_path / "Music"
    music.mkdir()

    result = tool("Test song", folder=str(music))

    wav, mp3 = rendered
    assert not wav.exists() and mp3.exists() and result["mp3"] == str(mp3)
    assert mp3.parent == music and mp3.name.startswith("test-song-")
    assert result["loudness_lufs"] == pytest.approx(-6.9, abs=0.3)  # both channels count
    assert result["true_peak_dbtp"] == pytest.approx(-6.0, abs=0.2)
    assert result["problems"] == [] and result["clipped_samples"] == 0
    modes = [p["mode"] for a, p in conn.sent if a == "transport.setLoopMode"]
    assert modes == ["song", "pattern"]  # song mode for the export, then back


def test_a_missing_folder_is_refused_before_rendering(fl, tmp_path):
    tool, _, rendered = fl
    assert "does not exist" in tool("x", folder=str(tmp_path / "nowhere"))["error"]
    assert rendered == []
