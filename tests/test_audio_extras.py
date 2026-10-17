"""Tempo, decoding, melody extraction, mix comparison and MIDI file export."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fl_studio_mcp.audio import decode
from fl_studio_mcp.audio.compare import compare
from fl_studio_mcp.audio.melody import extract_melody
from fl_studio_mcp.audio.tempo import estimate_tempo
from fl_studio_mcp.music.midi_file import PPQ, SHORTEST, midi_bytes, write_midi
from fl_studio_mcp.tools import (
    arrange,
    register_arrange_tools,
    register_audio_tools,
    register_music_tools,
)
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector
from tests.test_audio import write_wav

RATE = 44100


def clicks(bpm: float, seconds: float = 12.0, every: float = 1.0) -> np.ndarray:
    """A short noise burst every `every` beats, louder on each beat."""
    out = np.zeros(int(seconds * RATE))
    burst = np.random.default_rng(1).normal(0, 0.5, int(0.01 * RATE))
    step = 60 / bpm * every
    for i, start in enumerate(np.arange(0, seconds - 0.02, step)):
        at = int(start * RATE)
        out[at:at + len(burst)] += burst * (1.0 if (i * every) % 1 == 0 else 0.4)
    return out


def melody(bpm: float, notes: list[tuple[float, float, float]], seconds: float) -> np.ndarray:
    """Sine notes (midi, start beat, length in beats), each with a quick attack, a
    decay and a short gap before the next (as when a note is played again)."""
    out = np.zeros(int(seconds * RATE))
    for midi, start, length in notes:
        hz = 440 * 2 ** ((midi - 69) / 12)
        first, count = int(start * 60 / bpm * RATE), int(0.9 * length * 60 / bpm * RATE)
        t = np.arange(count) / RATE
        envelope = np.minimum(1, t / 0.005) * np.exp(-t * 3)
        out[first:first + count] += 0.5 * envelope * np.sin(2 * np.pi * hz * t)
    return out


# --- tempo -----------------------------------------------------------------


@pytest.mark.parametrize("bpm", [90.0, 128.0, 150.0, 174.0])
def test_tempo_of_regular_clicks(bpm):
    found = estimate_tempo(clicks(bpm), RATE)

    assert found["bpm"] == pytest.approx(bpm, abs=1.5)
    assert 0 < found["confidence"] <= 1
    assert all(isinstance(a["bpm"], float) for a in found["alternatives"])


def test_half_or_double_the_tempo_is_offered_as_an_alternative():
    # Quieter off-beat eighths fit 100 and 200 BPM alike: both are given.
    found = estimate_tempo(clicks(100, every=0.5), RATE)

    readings = [found["bpm"], *(a["bpm"] for a in found["alternatives"])]
    assert {100.0, 200.0} <= {round(bpm) for bpm in readings[:2]}


def test_tempo_needs_a_few_seconds():
    assert estimate_tempo(clicks(120, seconds=2), RATE) is None


# --- decoding ----------------------------------------------------------------


def test_a_plain_wav_is_read_without_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(decode, "find_ffmpeg", lambda: pytest.fail("ffmpeg not needed"))
    path = write_wav(tmp_path / "a.wav", np.zeros((100, 2)), RATE, 1, 16)

    samples, rate = decode.load_audio(path)

    assert samples.shape == (100, 2) and rate == RATE


def test_other_formats_need_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(decode, "find_ffmpeg", lambda: None)
    path = tmp_path / "song.mp3"
    path.write_bytes(b"not really")

    with pytest.raises(ValueError, match="ffmpeg"):
        decode.load_audio(path)
    with pytest.raises(OSError):
        decode.load_audio(tmp_path / "missing.mp3")


def test_ffmpeg_is_taken_from_the_environment(tmp_path, monkeypatch):
    program = tmp_path / "ffmpeg.exe"
    program.write_bytes(b"")
    monkeypatch.setenv("FL_MCP_FFMPEG", str(program))
    assert decode.find_ffmpeg() == str(program)
    monkeypatch.setenv("FL_MCP_FFMPEG", str(tmp_path / "nowhere.exe"))
    assert decode.find_ffmpeg() is None


# --- melody ------------------------------------------------------------------

TUNE = [(69, 0, 1), (72, 1, 0.5), (72, 1.5, 0.5), (67, 2, 2), (64, 4, 1)]


@pytest.mark.parametrize("bpm", [120.0, 150.0])
def test_melody_finds_every_note_on_the_grid(bpm):
    audio = melody(bpm, TUNE, seconds=5 * 60 / bpm + 0.5)

    found = extract_melody(audio, RATE, bpm)

    assert [(n["midi"], n["time"], n["duration"]) for n in found["notes"]] == TUNE
    assert found["tuning_cents"] == 0
    assert found["notes"][0]["note"] == "A4"


def test_melody_reports_and_removes_a_detuned_take():
    detuned = [(midi + 0.3, start, length) for midi, start, length in TUNE]

    found = extract_melody(melody(120, detuned, 3.0), RATE, 120)

    assert [n["midi"] for n in found["notes"]] == [m for m, _, _ in TUNE]
    assert found["tuning_cents"] == pytest.approx(30, abs=5)


def test_melody_needs_something_pitched():
    with pytest.raises(ValueError, match="pitched"):
        extract_melody(np.zeros(RATE), RATE, 120)
    with pytest.raises(ValueError):
        extract_melody(np.zeros(RATE), RATE, 0)


# --- comparing a mix with a reference ----------------------------------------


def analysis(lufs: float, bass: float, highs: float) -> dict:
    return {
        "loudness_lufs": lufs, "peak_dbfs": -1.0, "crest_factor_db": 8.0,
        "frequency_balance": {"bass": {"percent": bass}, "highs": {"percent": highs}},
        "stereo": {"correlation": 0.8, "side_to_mid_db": -12.0},
        "key": "F minor", "tempo": {"bpm": 150.0},
    }


def test_compare_lists_the_largest_differences_in_words():
    result = compare(analysis(-7.0, 40.0, 10.0), analysis(-9.0, 20.0, 10.5))

    assert result["loudness_lufs"] == {"mix": -7.0, "reference": -9.0, "difference": 2.0}
    assert result["frequency_balance"]["bass"]["difference_db"] == 3.0
    assert result["largest_differences"] == [
        "overall: 2.0 LU louder",
        "bass: 3.0 dB more of the energy than in the reference",
    ]  # the highs differ by 0.2 dB: not listed
    assert result["tempo"] == {"mix": 150.0, "reference": 150.0}


def test_compare_tolerates_missing_numbers():
    mono = {**analysis(-9.0, 20.0, 0.0), "stereo": None, "tempo": None}

    result = compare(mono, analysis(-9.0, 20.0, 10.0))

    assert "difference_db" not in result["frequency_balance"]["highs"]
    assert result["stereo_correlation"] == {"mix": None, "reference": 0.8}
    assert result["largest_differences"] == []


# --- MIDI files --------------------------------------------------------------


def parse_midi(data: bytes) -> tuple[tuple[int, int, int], list[list[tuple[int, bytes]]]]:
    """(format, track count, PPQ) and each track's (absolute tick, event) list."""
    assert data[:4] == b"MThd"
    header = struct.unpack(">HHH", data[8:14])
    tracks, at = [], 14
    while at < len(data):
        assert data[at:at + 4] == b"MTrk"
        (size,) = struct.unpack(">I", data[at + 4:at + 8])
        body, at = data[at + 8:at + 8 + size], at + 8 + size
        events, i, tick = [], 0, 0
        while i < len(body):
            delta = 0
            while True:
                delta = (delta << 7) | (body[i] & 0x7F)
                i += 1
                if not body[i - 1] & 0x80:
                    break
            tick += delta
            size = 3 + body[i + 2] if body[i] == 0xFF else 3
            events.append((tick, body[i:i + size]))
            i += size
        tracks.append(events)
    return header, tracks


def test_midi_file_has_a_conductor_track_and_one_track_per_part():
    data = midi_bytes([
        {"name": "Lead", "notes": [{"midi": 72, "time": 1.0, "duration": 0.5, "velocity": 1.0}]},
        {"name": "Kick", "drums": True, "notes": [{"midi": 36, "time": 0.0, "duration": 0.0}]},
    ], bpm=150, beats_per_bar=4)

    (fmt, count, ppq), tracks = parse_midi(data)

    assert (fmt, count, ppq) == (1, 3, PPQ)
    tempo = next(e for _, e in tracks[0] if e[:2] == b"\xff\x51")
    assert int.from_bytes(tempo[3:6], "big") == 400_000  # 150 BPM
    assert (0, b"\xff\x03\x04Lead") in tracks[1]
    assert (PPQ, bytes([0x90, 72, 127])) in tracks[1]
    assert (PPQ + PPQ // 2, bytes([0x80, 72, 0])) in tracks[1]
    # A step hit (duration 0) sounds for a 32nd, on the drum channel.
    assert (0, bytes([0x99, 36, 102])) in tracks[2]
    assert (SHORTEST, bytes([0x89, 36, 0])) in tracks[2]
    assert all(t[-1][1] == b"\xff\x2f\x00" for t in tracks)


def test_a_repeated_note_ends_before_it_starts_again():
    notes = [{"midi": 60, "time": 0.0, "duration": 1.0}, {"midi": 60, "time": 1.0, "duration": 1.0}]

    _, tracks = parse_midi(midi_bytes([{"name": "x", "notes": notes}], 120))

    at_beat = [e for tick, e in tracks[1] if tick == PPQ]
    assert [e[0] & 0xF0 for e in at_beat] == [0x80, 0x90]


@pytest.mark.parametrize(("tracks", "bpm"), [
    ([], 120),
    ([{"notes": [{"midi": 128, "time": 0}]}], 120),
    ([{"notes": [{"midi": 60, "time": -1}]}], 120),
    ([{"notes": [{"midi": "C4", "time": 0}]}], 120),
    ([{"notes": []}], 5),
])
def test_bad_midi_input_is_refused(tracks, bpm):
    with pytest.raises(ValueError):
        midi_bytes(tracks, bpm)


def test_write_midi_wants_a_mid_file(tmp_path):
    with pytest.raises(ValueError, match=".mid"):
        write_midi(tmp_path / "song.wav", [{"notes": []}], 120)

    result = write_midi(tmp_path / "out" / "song.mid", [{"notes": [{"midi": 60, "time": 0}]}], 120)

    assert result == {"path": str(tmp_path / "out" / "song.mid"), "tracks": 1, "notes": 1}
    assert (tmp_path / "out" / "song.mid").read_bytes()[:4] == b"MThd"


# --- tools -------------------------------------------------------------------


@pytest.fixture
def audio_tools(monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(connection, "get_connection", lambda: ScriptedConnection())
    collector = ToolCollector()
    register_audio_tools(collector)
    register_music_tools(collector)
    return collector.tools


def test_audio_compare_tool_measures_both_files(audio_tools, tmp_path):
    loud = np.column_stack([clicks(120)] * 2)
    mix = write_wav(tmp_path / "mix.wav", loud, RATE, 3, 32)
    reference = write_wav(tmp_path / "ref.wav", loud * 0.5, RATE, 3, 32)

    result = audio_tools["audio_compare"](str(mix), str(reference))

    assert result["loudness_lufs"]["difference"] == pytest.approx(6.0, abs=0.1)
    assert result["largest_differences"][0].startswith("overall: 6.0 LU louder")
    assert "error" in audio_tools["audio_compare"](str(mix), str(tmp_path / "missing.wav"))


def test_audio_extract_melody_tool(audio_tools, tmp_path):
    audio = melody(120, TUNE, 3.0)
    path = write_wav(tmp_path / "take.wav", np.column_stack([audio] * 2), RATE, 3, 32)

    result = audio_tools["audio_extract_melody"](str(path), bpm=120)

    assert [n["midi"] for n in result["notes"]] == [m for m, _, _ in TUNE]
    assert "short" in audio_tools["audio_extract_melody"](str(path))["error"]  # 3 s: no tempo


def test_music_export_midi_tool(audio_tools, tmp_path):
    notes = [{"midi": 60, "time": 0.0, "duration": 1.0}]

    done = audio_tools["music_export_midi"]([{"name": "A", "notes": notes}],
                                            str(tmp_path / "a.mid"), bpm=140)

    assert done["notes"] == 1 and (tmp_path / "a.mid").is_file()
    assert "error" in audio_tools["music_export_midi"]([], str(tmp_path / "b.mid"), bpm=140)


# --- fl_export_midi ------------------------------------------------------------

KICK = [{"midi": 36, "time": float(beat), "duration": 0.0} for beat in range(4)]
LEAD = [{"midi": 76, "time": 0.0, "duration": 8.0}]


@pytest.fixture
def export(monkeypatch) -> tuple[Any, ScriptedConnection]:
    conn = ScriptedConnection({"project.getInfo": {
        "bpm": 150.0, "beats_per_bar": 4, "current_pattern": 2,
        "patterns": [{"index": 1, "name": "Drums", "length_beats": 4.0},
                     {"index": 2, "name": "Lead", "length_beats": 8.0}],
        "channels": [{"index": 0, "name": "Kick"}, {"index": 1, "name": "Lead"},
                     {"index": 2, "name": "Auto", "is_automation": True}],
    }})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    rolls = {(0, 1): KICK, (1, 2): LEAD, (2, 1): KICK}
    monkeypatch.setattr(arrange, "read_notes", lambda c, p: rolls.get((c, p), []))
    collector = ToolCollector()
    register_arrange_tools(collector)
    return collector.tools["fl_export_midi"], conn


def test_fl_export_midi_writes_the_active_pattern(export, tmp_path):
    tool, conn = export

    result = tool(str(tmp_path / "lead.mid"))

    assert result == {"path": str(tmp_path / "lead.mid"), "tracks": 1, "notes": 1,
                      "channels": ["Lead"]}
    assert conn.sent[-1] == ("patterns.select", {"index": 2})  # the active pattern again


def test_fl_export_midi_lays_a_song_out_and_skips_automation(export, tmp_path):
    tool, _ = export

    result = tool(str(tmp_path / "song.mid"),
                  sections=[{"bars": 2, "patterns": [1]}, {"bars": 2, "patterns": [1, 2]}])

    assert result["channels"] == ["Kick", "Lead"]
    assert result["notes"] == 16 + 1
    _, tracks = parse_midi((tmp_path / "song.mid").read_bytes())
    lead_on = [tick for tick, e in tracks[2] if e[0] & 0xF0 == 0x90]
    assert lead_on == [8 * PPQ]  # the lead starts with the second section


def test_fl_export_midi_errors(export, tmp_path):
    tool, conn = export

    assert "empty" in tool(str(tmp_path / "a.mid"), pattern=7)["error"]
    assert "notes" in tool(str(tmp_path / "a.mid"), pattern=1, channels=[1])["error"]
    assert ".mid" in tool(str(tmp_path / "a.txt"))["error"]
    assert conn.sent[-1] == ("patterns.select", {"index": 2})
    assert not Path(tmp_path / "a.mid").exists()
