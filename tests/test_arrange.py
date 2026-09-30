"""Arranging patterns into one long pattern: the layout maths and the fl_arrange tool."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.music.arrange import Section, layout_sections, tile_notes
from fl_studio_mcp.tools import arrange, piano_roll, register_arrange_tools
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector

# --- layout ----------------------------------------------------------------


def test_sections_are_laid_out_back_to_back_in_beats():
    laid_out = layout_sections(
        [{"name": "intro", "bars": 2, "patterns": [1]}, {"bars": 1, "patterns": [1, 3, 1]}], 4
    )

    assert laid_out == [
        Section("intro", 0.0, 8.0, (1,)),
        Section("section 2", 8.0, 4.0, (1, 3)),  # unnamed; a repeated pattern counts once
    ]
    assert laid_out[-1].end == 12.0


def test_a_section_without_patterns_is_silence():
    assert layout_sections([{"bars": 4}], 3) == [Section("section 1", 0.0, 12.0, ())]


@pytest.mark.parametrize("sections", [
    [],
    ["intro"],
    [{"bars": 0, "patterns": [1]}],
    [{"bars": 2.5, "patterns": [1]}],
    [{"bars": True, "patterns": [1]}],
    [{"patterns": [1]}],
    [{"bars": 2, "patterns": 1}],
    [{"bars": 2, "patterns": [0]}],
    [{"bars": 2, "patterns": ["1"]}],
])
def test_bad_sections_are_rejected(sections):
    with pytest.raises(ValueError):
        layout_sections(sections, 4)


# --- looping notes ---------------------------------------------------------


def test_notes_are_looped_to_fill_the_span():
    notes = [{"midi": 60, "time": 0.0, "duration": 1.0, "velocity": 0.5, "slide": True}]

    tiled = tile_notes(notes, source_length=4.0, start=8.0, length=8.0)

    assert [(n["time"], n["duration"]) for n in tiled] == [(8.0, 1.0), (12.0, 1.0)]
    assert all(n["velocity"] == 0.5 and n["slide"] is True for n in tiled)
    assert notes[0]["time"] == 0.0  # the source notes are not modified


def test_the_last_loop_is_cut_at_the_end_of_the_span():
    notes = [{"midi": 60, "time": 0.0, "duration": 4.0}, {"midi": 62, "time": 3.0, "duration": 1.0}]

    tiled = tile_notes(notes, source_length=4.0, start=0.0, length=6.0)

    # second loop: the long note is shortened to 2 beats, the note at beat 7 is past the end
    assert [(n["midi"], n["time"], n["duration"]) for n in tiled] == [
        (60, 0.0, 4.0), (62, 3.0, 1.0), (60, 4.0, 2.0),
    ]


def test_a_note_running_past_the_loop_point_is_cut_there():
    tiled = tile_notes([{"midi": 60, "time": 3.0, "duration": 4.0}], 4.0, 0.0, 8.0)

    assert [(n["time"], n["duration"]) for n in tiled] == [(3.0, 1.0), (7.0, 1.0)]


@pytest.mark.parametrize("note", [
    {"midi": 60, "time": "0", "duration": 1.0}, {"midi": 60, "time": 0, "duration": None},
])
def test_notes_with_bad_times_are_rejected(note):
    with pytest.raises(ValueError, match="must be a number"):
        tile_notes([note], 4.0, 0.0, 4.0)


def test_step_hits_keep_their_zero_duration():
    tiled = tile_notes([{"midi": 60, "time": 1.0, "duration": 0.0}], 4.0, 0.0, 4.0)

    assert tiled == [{"midi": 60, "time": 1.0, "duration": 0.0}]


def test_notes_outside_the_source_loop_are_dropped():
    notes = [{"midi": 60, "time": 4.0, "duration": 1.0}, {"midi": 61, "time": -1.0, "duration": 1}]

    assert tile_notes(notes, source_length=4.0, start=0.0, length=8.0) == []


def test_a_pattern_without_length_cannot_be_looped():
    with pytest.raises(ValueError):
        tile_notes([], source_length=0, start=0.0, length=4.0)


# --- the tool --------------------------------------------------------------

KICK = [{"midi": 60, "time": float(beat), "duration": 0.0} for beat in range(4)]
LEAD = [{"midi": 76, "time": 0.0, "duration": 8.0}]


class FakeRolls:
    """Piano rolls by (channel, pattern), standing in for read_notes/write_notes."""

    def __init__(self) -> None:
        self.rolls: dict[tuple[int, int], list[dict]] = {(0, 1): KICK, (1, 2): LEAD}
        self.written: dict[tuple[int, int], list[dict]] = {}
        self.fail_on_channel: int | None = None

    def read(self, channel: int, pattern: int) -> list[dict]:
        if channel == self.fail_on_channel:
            raise ValueError("FL Studio did not respond.")
        return self.rolls.get((channel, pattern), [])

    def write(self, notes: list[dict], channel: int, pattern: int) -> None:
        self.written[(channel, pattern)] = notes


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection({"project.getInfo": {
        "beats_per_bar": 4,
        "patterns": [
            {"index": 1, "name": "Drums", "length_beats": 4.0},
            {"index": 2, "name": "Lead", "length_beats": 8.0},
        ],
        "channels": [{"index": 0, "name": "Kick"}, {"index": 1, "name": "Lead"},
                     {"index": 2, "name": "Pad"}],
    }})
    conn.results["channels.getAll"] = {"channels": conn.results["project.getInfo"]["channels"]}
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def rolls(monkeypatch) -> FakeRolls:
    fake = FakeRolls()
    monkeypatch.setattr(arrange, "read_notes", fake.read)
    monkeypatch.setattr(arrange, "write_notes", fake.write)
    return fake


@pytest.fixture
def fl_arrange(fl, rolls) -> Any:
    collector = ToolCollector()
    register_arrange_tools(collector)
    return collector.tools["fl_arrange"]


SONG = [
    {"name": "intro", "bars": 2, "patterns": [1]},
    {"name": "break", "bars": 1, "patterns": []},
    {"name": "climax", "bars": 2, "patterns": [1, 2]},
]


def test_arrange_writes_each_channels_song_into_the_target_pattern(fl_arrange, rolls):
    result = fl_arrange(SONG, target_pattern=5, name="Song")

    kick_times = [n["time"] for n in rolls.written[(0, 5)]]
    assert kick_times == [float(b) for b in (*range(0, 8), *range(12, 20))]  # silent in the break
    assert [(n["time"], n["duration"]) for n in rolls.written[(1, 5)]] == [(12.0, 8.0)]
    assert (2, 5) not in rolls.written  # the pad has nothing to play
    assert result == {
        "pattern": 5,
        "total_bars": 5,
        "sections": [
            {"name": "intro", "start_bar": 1, "bars": 2, "patterns": [1]},
            {"name": "break", "start_bar": 3, "bars": 1, "patterns": []},
            {"name": "climax", "start_bar": 4, "bars": 2, "patterns": [1, 2]},
        ],
        "notes_written": {"Kick": 16, "Lead": 1},
    }


def test_arrange_leaves_the_target_pattern_active_and_renamed(fl_arrange, fl):
    fl_arrange(SONG, target_pattern=5, name="Song")

    assert fl.sent[-2:] == [
        ("patterns.select", {"index": 5}),
        ("patterns.rename", {"index": 5, "name": "Song"}),
    ]


def test_arranging_again_clears_channels_that_are_now_silent(fl_arrange, fl, rolls):
    fl.results["project.getInfo"]["patterns"].append(
        {"index": 5, "name": "Song", "length_beats": 20.0}
    )

    fl_arrange([{"bars": 1, "patterns": [1]}], target_pattern=5)

    assert rolls.written[(1, 5)] == [] and rolls.written[(2, 5)] == []
    assert len(rolls.written[(0, 5)]) == 4


def test_arrange_can_be_limited_to_some_channels_by_role(fl_arrange, rolls):
    result = fl_arrange(SONG, target_pattern=5, channels=["kick"])

    assert list(rolls.written) == [(0, 5)]
    assert result["notes_written"] == {"Kick": 16}


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"sections": [], "target_pattern": 5}, "At least one section"),
    ({"sections": SONG, "target_pattern": 1}, "is a source"),
    ({"sections": SONG, "target_pattern": 0}, "target_pattern must be"),
    ({"sections": [{"bars": 1, "patterns": [7]}], "target_pattern": 5}, "[7] are empty"),
    ({"sections": SONG, "target_pattern": 5, "channels": ["tuba"]}, "tuba"),
    ({"sections": SONG, "target_pattern": 5, "channels": []}, "No channels"),
])
def test_arrange_rejects_bad_requests_before_touching_the_piano_roll(
    fl_arrange, rolls, kwargs, message
):
    assert message in fl_arrange(**kwargs)["error"]
    assert rolls.written == {}


def test_channels_sharing_a_name_are_counted_separately(fl_arrange, fl, rolls):
    fl.results["project.getInfo"]["channels"][1]["name"] = "Kick"
    rolls.rolls[(1, 1)] = KICK

    result = fl_arrange([{"bars": 1, "patterns": [1]}], target_pattern=5)

    assert result["notes_written"] == {"Kick": 4, "Kick (channel 1)": 4}


def test_arrange_reports_where_it_stopped(fl_arrange, rolls):
    rolls.fail_on_channel = 1

    error = fl_arrange(SONG, target_pattern=5)["error"]

    assert "channel 1" in error and "did not respond" in error and "Kick" in error


def test_arrange_passes_on_a_project_read_failure(fl_arrange, fl):
    fl.results["project.getInfo"] = {"error": "FL is not answering"}

    assert fl_arrange(SONG, target_pattern=5) == {"error": "FL is not answering"}


# --- piano roll helpers used by the tool -----------------------------------


def test_read_back_pitch_offset_survives_a_resend():
    prepared = piano_roll._prepare_note({"midi": 60, "duration": 1.0, "pitchofs": -12})

    assert prepared["pitchofs"] == -12


def test_fine_pitch_wins_over_a_read_back_pitch_offset():
    note = {"midi": 60, "duration": 1.0, "pitchofs": -12, "fine_pitch": 50}

    assert piano_roll._prepare_note(note)["pitchofs"] == 5


def test_an_out_of_range_pitch_offset_is_rejected():
    with pytest.raises(ValueError, match="pitchofs"):
        piano_roll._prepare_note({"midi": 60, "duration": 1.0, "pitchofs": 500})
