"""Group mute/solo/colour tools, colour parsing, quantizing notes, cloning and markers."""

from __future__ import annotations

import pytest

from fl_studio_mcp.music.theory import scale_intervals
from fl_studio_mcp.tools import groups, piano_roll
from fl_studio_mcp.tools.groups import register_group_tools
from fl_studio_mcp.tools.patterns import register_pattern_tools
from fl_studio_mcp.tools.piano_roll import quantized, register_piano_roll_tools
from fl_studio_mcp.utils.colors import parse_color, rgb
from fl_studio_mcp.utils.roles import matches_group
from tests.fakes import ScriptedConnection, ToolCollector

TRACKS = [
    {"index": 0, "name": "Master", "channels": []},
    {"index": 1, "name": "Insert 1", "channels": ["808 Kick"]},
    {"index": 2, "name": "Drums", "channels": []},
    {"index": 3, "name": "Insert 3", "channels": ["Lead"]},
]
CHANNELS = [{"index": 0, "name": "808 Kick"}, {"index": 1, "name": "Lead"},
            {"index": 2, "name": "Kick Alt"}]


@pytest.fixture
def conn(monkeypatch):
    connection = ScriptedConnection({
        "mixer.getAllTracks": {"tracks": TRACKS},
        "channels.getAll": {"channels": CHANNELS},
        "mixer.muteAllExcept": {"kept": [1], "muted": [2, 3]},
        "patterns.clone": {"index": 4, "source": 2, "name": "Pattern 4"},
        "patterns.rename": {"index": 4, "name": "Verse 2"},
    })
    monkeypatch.setattr("fl_studio_mcp.utils.connection.get_connection", lambda: connection)
    return connection


def _tools(register):
    collector = ToolCollector()
    register(collector)
    return collector.tools


@pytest.mark.parametrize("spec, expected", [
    ("red", 0xE53935), ("Light Blue", 0x4FC3F7), ("light_blue", 0x4FC3F7),
    ("#ff8800", 0xFF8800), ("FF8800", 0xFF8800), ("0xff8800", 0xFF8800), ("#f80", 0xFF8800),
])
def test_parse_color(spec, expected):
    assert parse_color(spec) == expected


@pytest.mark.parametrize("spec", ["", "reddish", "#ff88", "#gg0000"])
def test_parse_color_rejects_unknown(spec):
    with pytest.raises(ValueError):
        parse_color(spec)


def test_rgb_splits_components():
    assert rgb(0x123456) == (0x12, 0x34, 0x56)


def test_group_matches_whole_words_in_any_name():
    assert matches_group("kick", ["Insert 1", "808 Kick"])
    assert matches_group("snare, kick", ["Kick Alt"])
    assert not matches_group("kick", ["Kicks"])
    with pytest.raises(ValueError):
        matches_group(" , ", ["Kick"])


def test_resolve_tracks_by_track_name_and_routed_channel(conn):
    assert groups.resolve_tracks("kick, drums", None) == [1, 2]
    assert groups.resolve_tracks(None, [5]) == [5]
    with pytest.raises(ValueError):
        groups.resolve_tracks("pad", None)
    with pytest.raises(ValueError):
        groups.resolve_tracks(None, None)


def test_mute_tracks_mutes_each_matching_track(conn):
    result = _tools(register_group_tools)["fl_mute_tracks"](group="kick")

    assert result == {"tracks": [1], "muted": True}
    assert ("mixer.muteTrack", {"track": 1, "muted": True}) in conn.sent


def test_solo_tracks_mutes_all_the_others(conn):
    result = _tools(register_group_tools)["fl_solo_tracks"](group="kick")

    assert result == {"kept": [1], "muted": [2, 3]}
    assert ("mixer.muteAllExcept", {"tracks": [1]}) in conn.sent


def test_set_colors_colours_tracks_and_channels_of_a_group(conn):
    result = _tools(register_group_tools)["fl_set_colors"]("#102030", group="kick")

    assert result == {"color": "#102030", "tracks": [1], "channels": [0, 2]}
    assert ("mixer.setTrackColor", {"track": 1, "r": 16, "g": 32, "b": 48}) in conn.sent
    assert ("channels.setColor", {"index": 2, "r": 16, "g": 32, "b": 48}) in conn.sent


def test_set_colors_needs_a_target_and_a_known_colour(conn):
    tools = _tools(register_group_tools)
    assert "error" in tools["fl_set_colors"]("red")
    assert "error" in tools["fl_set_colors"]("reddish", tracks=[1])
    assert "error" in tools["fl_set_colors"]("red", group="pad")


def test_quantized_moves_starts_and_keeps_other_fields():
    notes = [{"midi": 60, "time": 0.27, "duration": 0.4, "velocity": 0.5},
             {"midi": 62, "time": 1.1, "duration": 0}]

    result = quantized(notes, 0.25)

    assert result[0] == {"midi": 60, "time": 0.25, "duration": 0.4, "velocity": 0.5}
    assert result[1]["time"] == 1.0 and result[1]["duration"] == 0
    assert notes[0]["time"] == 0.27  # inputs untouched


def test_quantized_half_strength_and_ends():
    result = quantized([{"time": 0.2, "duration": 0.65}], 0.25, strength=0.5)
    assert result[0]["time"] == pytest.approx(0.225)

    short = quantized([{"time": 0.0, "duration": 0.1}], 0.25, ends=True)
    assert short[0]["duration"] == pytest.approx(0.1)  # a short note isn't lengthened
    long = quantized([{"time": 0.0, "duration": 0.9}], 0.25, ends=True)
    assert long[0]["duration"] == pytest.approx(1.0)


def test_quantized_rejects_bad_grid():
    with pytest.raises(ValueError):
        quantized([], 0)


def test_quantize_tool_writes_only_when_something_moved(monkeypatch):
    written = []
    monkeypatch.setattr(piano_roll, "read_notes", lambda c, p: [{"midi": 60, "time": 0.3,
                                                                 "duration": 1.0}])
    monkeypatch.setattr(piano_roll, "write_notes", lambda n, c, p: written.append((n, c, p)))
    tool = _tools(register_piano_roll_tools)["fl_quantize_notes"]

    assert tool(2, 1) == {"notes": 1, "moved": 1}
    assert written[0][0][0]["time"] == 0.25 and written[0][1:] == (2, 1)

    written.clear()
    monkeypatch.setattr(piano_roll, "read_notes", lambda c, p: [{"time": 0.5, "duration": 1}])
    assert tool(2, 1) == {"notes": 1, "moved": 0}
    assert not written


def test_clone_pattern_renames_the_copy(conn):
    result = _tools(register_pattern_tools)["fl_clone_pattern"](2, name="Verse 2")

    assert result == {"index": 4, "source": 2, "name": "Verse 2"}
    assert ("patterns.rename", {"index": 4, "name": "Verse 2"}) in conn.sent


def test_add_marker_sends_bar_and_name(conn):
    _tools(register_pattern_tools)["fl_add_marker"](17, "Break")

    assert ("arrangement.addMarker", {"bar": 17, "name": "Break"}) in conn.sent


@pytest.mark.parametrize("name, intervals", [
    ("bhairav", (0, 1, 4, 5, 7, 8, 11)), ("hijaz", (0, 1, 4, 5, 7, 8, 10)),
    ("hirajoshi", (0, 2, 3, 7, 8)), ("super_locrian", (0, 1, 3, 4, 6, 8, 10)),
    ("octatonic", (0, 2, 3, 5, 6, 8, 9, 11)),
])
def test_more_scales(name, intervals):
    assert scale_intervals(name) == intervals
