"""Sung vocals: the OpenUtau project text, finding ourender, and the tools."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fl_studio_mcp.audio import singing
from fl_studio_mcp.tools import vocals
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector

NOTES = [{"midi": 69, "time": 0, "duration": 1, "lyric": "hold"},
         {"midi": 72, "time": 1, "duration": 0.5, "lyric": "light"},
         {"midi": 74, "time": 1.5, "duration": 0.5, "lyric": "+"}]


def test_the_project_holds_every_note_in_ticks():
    text = singing.ustx_text("Verse 1", NOTES, 130, 4, "Canary", "Phon", "DIFFSINGER")

    assert "resolution: 480" in text and "bpm: 130" in text
    assert ("- singer: 'Canary'\n  phonemizer: 'Phon'\n"
            "  renderer_settings: {renderer: DIFFSINGER}") in text
    assert "  - position: 480\n    duration: 240\n    tone: 72\n    lyric: 'light'" in text
    assert "  - position: 720\n    duration: 240\n    tone: 74\n    lyric: '+'" in text
    assert "  duration: 960\n  notes:" in text
    assert text.endswith("  curves: []\nwave_parts: []\n")


def test_names_stay_one_value_whatever_they_hold():
    text = singing.ustx_text("x", NOTES, 120, 4, "S", "P\nrenderer_settings: {x}")

    assert "  phonemizer: 'P\nrenderer_settings: {x}'\n" in text


def test_a_lyric_with_a_quote_is_escaped():
    text = singing.ustx_text("it's", [{**NOTES[0], "lyric": "don't"}], 120, 4, "S", "P")

    assert "name: 'it''s'" in text and "lyric: 'don''t'" in text


@pytest.mark.parametrize(("notes", "message"), [
    ([], "Give the notes"),
    ([{**NOTES[0], "lyric": ""}], "no lyric"),
    ([{**NOTES[0], "lyric": "+"}], "starts a word"),
    ([NOTES[0], {**NOTES[1], "time": 0.5}], "overlaps"),
    ([{**NOTES[0], "midi": 200}], "out of range"),
    ([{"midi": 60, "lyric": "a"}], "needs midi, time and duration"),
])
def test_bad_notes_are_refused(notes, message):
    with pytest.raises(singing.SingingError, match=message):
        singing.ustx_text("x", notes, 120, 4, "S", "P")


def test_the_renderer_follows_the_kind_of_singer():
    assert singing.renderer_for("DiffSinger") == "DIFFSINGER"
    assert singing.renderer_for("Classic") == "CLASSIC"


def test_ourender_is_found_from_the_environment(tmp_path, monkeypatch):
    program = tmp_path / "ourender.exe"
    program.write_bytes(b"")
    monkeypatch.setenv("FL_MCP_OURENDER", str(program))
    assert singing.find_ourender() == str(program)
    monkeypatch.setenv("FL_MCP_OURENDER", str(tmp_path / "missing.exe"))
    assert singing.find_ourender() is None
    with pytest.raises(singing.SingingError, match="FL_MCP_OURENDER"):
        singing.list_singers()


def test_the_singer_list_is_parsed(monkeypatch):
    monkeypatch.setattr(singing, "find_ourender", lambda: "ourender")
    out = ("singer\tCanary\tDiffSinger\tC:\\Singers\\Canary\n"
           "phonemizer\tOpenUtau.Core.DiffSinger.DiffSingerEnglishPhonemizer\tEnglish\n")
    monkeypatch.setattr(singing.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))

    assert singing.list_singers() == {
        "singers": [{"id": "Canary", "type": "DiffSinger"}],
        "phonemizers": [{"id": "OpenUtau.Core.DiffSinger.DiffSingerEnglishPhonemizer",
                         "name": "English"}],
    }


@pytest.fixture
def tool(monkeypatch, tmp_path):
    monkeypatch.setattr(connection, "get_connection",
                        lambda: ScriptedConnection({"project.getTempo": {"bpm": 150.0}}))
    monkeypatch.setattr(vocals, "_vocal_dir", lambda: tmp_path)
    monkeypatch.setattr(singing, "list_singers", lambda: {
        "singers": [{"id": "Canary", "type": "DiffSinger"}], "phonemizers": []})
    sung = []

    def sing(project: Path, wav: Path, auto_pitch: bool = True) -> str:
        sung.append((project.read_text(encoding="utf-8"), auto_pitch))
        wav.write_bytes(b"RIFF")
        return "part: 3 notes"

    monkeypatch.setattr(singing, "sing", sing)
    collector = ToolCollector()
    vocals.register_vocal_tools(collector)
    return collector.tools, sung, tmp_path


def test_vocal_render_uses_the_projects_tempo_and_the_installed_singer(tool):
    tools, sung, folder = tool

    result = tools["vocal_render"](NOTES, "Verse: 1")

    assert result["path"] == str(folder / "Verse_ 1.wav")
    assert result["browser_path"] == "Project bones/FL-MCP vocals/Verse_ 1.wav"
    text, auto_pitch = sung[0]
    assert "bpm: 150" in text and "singer: 'Canary'" in text and "EnglishPhonemizer" in text
    assert auto_pitch is True


def test_vocal_render_refuses_an_unknown_singer_or_bad_notes(tool):
    tools, sung, _ = tool

    assert "installed: Canary" in tools["vocal_render"](NOTES, "a", singer="Other")["error"]
    assert "overlaps" in tools["vocal_render"]([NOTES[0], NOTES[0]], "a", bpm=120)["error"]
    assert "letters or digits" in tools["vocal_render"](NOTES, "...", bpm=120)["error"]
    assert sung == []


def test_a_failed_render_is_tried_again(monkeypatch, tmp_path):
    runs = []
    wav = tmp_path / "v.wav"

    def run(arguments, timeout):
        runs.append(arguments)
        if len(runs) == 2:
            wav.write_bytes(b"RIFF")
            return subprocess.CompletedProcess(arguments, 0, "part: 1 note", "")
        return subprocess.CompletedProcess(arguments, 1, "", "pitch model failed")

    monkeypatch.setattr(singing, "_run", run)

    assert singing.sing(tmp_path / "v.ustx", wav) == "part: 1 note"
    assert len(runs) == 2 and runs[0][-1] == "--auto-pitch"
    wav.unlink()
    runs.clear()
    monkeypatch.setattr(singing, "RENDER_ATTEMPTS", 1)
    with pytest.raises(singing.SingingError, match="auto_pitch=False"):
        singing.sing(tmp_path / "v.ustx", wav)
