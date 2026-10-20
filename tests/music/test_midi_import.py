"""Reading standard MIDI files into notes in fl_send_notes format."""

from __future__ import annotations

import struct

import pytest

from fl_studio_mcp.music.midi_file import midi_bytes, parse_midi, read_midi


def test_round_trip_with_the_writer():
    data = midi_bytes([
        {"name": "Lead", "notes": [{"midi": 60, "time": 0, "duration": 1, "velocity": 1.0},
                                   {"midi": 64, "time": 1.5, "duration": 0.5}]},
        {"name": "Drums", "drums": True, "notes": [{"midi": 36, "time": 0, "duration": 0.25}]},
    ], bpm=150, beats_per_bar=3)

    song = parse_midi(data)

    assert song["bpm"] == pytest.approx(150, abs=0.01)
    assert song["beats_per_bar"] == 3
    lead, drums = song["tracks"]
    assert (lead["name"], lead["drums"]) == ("Lead", False)
    assert [(n["midi"], n["time"], n["duration"]) for n in lead["notes"]] == [
        (60, 0, 1), (64, 1.5, 0.5)]
    assert lead["notes"][0]["velocity"] == 1.0
    assert drums["drums"] is True and drums["notes"][0]["midi"] == 36


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _file(tracks: list[bytes], ppq: int = 96, kind: int = 1) -> bytes:
    header = b"MThd" + struct.pack(">IHHH", 6, kind, len(tracks), ppq)
    return header + b"".join(b"MTrk" + struct.pack(">I", len(t)) + t for t in tracks)


def test_running_status_and_velocity_zero_note_off():
    # One note-on, then running status: the same pitch at velocity 0 ends it.
    body = (_vlq(0) + bytes([0x90, 62, 100]) + _vlq(48) + bytes([62, 0])
            + _vlq(0) + bytes([0xFF, 0x2F, 0]))

    notes = parse_midi(_file([body]))["tracks"][0]["notes"]

    assert [(n["midi"], n["time"], n["duration"]) for n in notes] == [(62, 0, 0.5)]
    assert notes[0]["velocity"] == pytest.approx(100 / 127, abs=1e-3)


def test_a_single_track_file_is_split_by_channel():
    body = (_vlq(0) + bytes([0x90, 60, 90]) + _vlq(0) + bytes([0x99, 36, 90])
            + _vlq(96) + bytes([0x80, 60, 0]) + _vlq(0) + bytes([0x89, 36, 0])
            + _vlq(0) + bytes([0xFF, 0x2F, 0]))

    tracks = parse_midi(_file([body], kind=0))["tracks"]

    assert [(t["channel"], t["drums"], len(t["notes"])) for t in tracks] == [
        (0, False, 1), (9, True, 1)]


def test_sysex_and_other_events_are_skipped():
    body = (_vlq(0) + bytes([0xF0, 3, 1, 2, 0xF7]) + _vlq(0) + bytes([0xB0, 7, 100])
            + _vlq(0) + bytes([0xC0, 5]) + _vlq(0) + bytes([0x90, 70, 80])
            + _vlq(96) + bytes([0x80, 70, 0]) + _vlq(0) + bytes([0xFF, 0x2F, 0]))

    notes = parse_midi(_file([body]))["tracks"][0]["notes"]

    assert [(n["midi"], n["duration"]) for n in notes] == [(70, 1)]


def test_tempo_defaults_to_120_without_a_tempo_event():
    assert parse_midi(_file([_vlq(0) + bytes([0xFF, 0x2F, 0])]))["bpm"] == 120


@pytest.mark.parametrize("data", [b"", b"RIFF1234", _file([b"\x00\x90"])[:-1]])
def test_broken_files_are_errors(data):
    with pytest.raises(ValueError):
        parse_midi(data)


def test_smpte_timing_is_refused():
    header = b"MThd" + struct.pack(">IHHh", 6, 1, 0, -6000)
    with pytest.raises(ValueError, match="SMPTE"):
        parse_midi(header)


def test_read_midi_reads_a_file(tmp_path):
    path = tmp_path / "a.mid"
    path.write_bytes(midi_bytes([{"name": "A", "notes": [{"midi": 60, "duration": 1}]}], 128))

    assert read_midi(path)["tracks"][0]["notes"][0]["midi"] == 60
