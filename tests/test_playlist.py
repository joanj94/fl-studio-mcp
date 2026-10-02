"""Building a song in the playlist: where clips go, and the clicks that put them there."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.music.arrange import clip_bars, layout_sections
from fl_studio_mcp.tools import register_arrange_tools
from fl_studio_mcp.utils import connection, fl_playlist, fl_windows
from tests.fake_playlist import FakePlaylist
from tests.fakes import ToolCollector

# Pattern 1: one bar (drums), 2: four bars (riff), 3: eight bars (chords).
PATTERN_BARS = {1: 1, 2: 4, 3: 8}
SONG = [
    {"name": "intro", "bars": 4, "patterns": [1]},
    {"name": "break", "bars": 8, "patterns": [3]},
    {"name": "drop", "bars": 8, "patterns": [1, 2]},
]


def _sections(sections: list[dict]):
    return layout_sections(sections, beats_per_bar=4)


def _lengths() -> dict[int, float]:
    return {pattern: bars * 4.0 for pattern, bars in PATTERN_BARS.items()}


# --- where the clips go ------------------------------------------------------


def test_each_pattern_is_repeated_to_fill_its_sections():
    assert clip_bars(_sections(SONG), _lengths(), 4) == {
        1: [1, 2, 3, 4, 13, 14, 15, 16, 17, 18, 19, 20],
        3: [5],
        2: [13, 17],
    }


def test_a_section_without_patterns_is_a_gap():
    sections = _sections([{"bars": 2, "patterns": [1]}, {"bars": 2, "patterns": []},
                          {"bars": 1, "patterns": [1]}])

    assert clip_bars(sections, _lengths(), 4) == {1: [1, 2, 5]}


def test_a_pattern_that_does_not_fit_its_section_is_refused():
    with pytest.raises(ValueError, match="Pattern 3 is 8 bars long and doesn't fit section 'a'"):
        clip_bars(_sections([{"name": "a", "bars": 4, "patterns": [3]}]), _lengths(), 4)
    with pytest.raises(ValueError, match="whole number of repeats"):
        clip_bars(_sections([{"bars": 6, "patterns": [2]}]), _lengths(), 4)


def test_an_empty_or_unknown_pattern_is_refused():
    with pytest.raises(ValueError, match="Pattern 9 is empty"):
        clip_bars(_sections([{"bars": 4, "patterns": [9]}]), _lengths(), 4)


def test_a_pattern_can_be_started_every_so_many_bars():
    sections = _sections([
        {"bars": 4, "patterns": [1]},
        {"bars": 16, "patterns": [{"pattern": 1, "every_bars": 8}, 2]},
        {"bars": 8, "patterns": [{"pattern": 2, "every_bars": 8}]},
    ])

    assert clip_bars(sections, _lengths(), 4) == {1: [1, 2, 3, 4, 5, 13], 2: [5, 9, 13, 17, 21]}


def test_a_gap_shorter_than_the_pattern_or_a_section_too_short_is_refused():
    with pytest.raises(ValueError, match="can't start every 2 bars"):
        clip_bars(_sections([{"bars": 8, "patterns": [{"pattern": 2, "every_bars": 2}]}]),
                  _lengths(), 4)
    with pytest.raises(ValueError, match="doesn't fit"):
        clip_bars(_sections([{"bars": 4, "patterns": [{"pattern": 3, "every_bars": 8}]}]),
                  _lengths(), 4)


@pytest.mark.parametrize("entry", [0, "1", {"pattern": 1}, {"pattern": 1, "every_bars": 0},
                                   {"pattern": 1, "every_bars": 4, "x": 1}])
def test_a_pattern_entry_must_be_an_index_or_a_pattern_with_its_gap(entry):
    with pytest.raises(ValueError, match="'patterns' must be a list"):
        _sections([{"bars": 4, "patterns": [entry]}])


def test_a_pattern_of_part_of_a_bar_takes_a_whole_bar():
    assert clip_bars(_sections([{"bars": 2, "patterns": [1]}]), {1: 2.0}, 4) == {1: [1, 2]}


# --- the tool ------------------------------------------------------------------


@pytest.fixture
def fl(monkeypatch) -> FakePlaylist:
    fake = FakePlaylist(PATTERN_BARS)
    monkeypatch.setattr(connection, "get_connection", lambda: fake)
    monkeypatch.setattr(fl_playlist.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_playlist, "MENU_TIMEOUT", 0.0)
    monkeypatch.setattr(fl_playlist, "CHECK_TIMEOUT", 0.0)
    monkeypatch.setattr(fl_playlist, "SCROLL_TIMEOUT", 0.0)
    # Only the pause given to a scroll that can't be checked lets one finish.
    monkeypatch.setattr(
        fl_playlist.time, "sleep",
        lambda seconds: fake.settle() if seconds >= fl_playlist.SCROLL_SETTLE else None,
    )
    monkeypatch.setattr(fl_windows, "playlist_panel", lambda: fake.panel)
    monkeypatch.setattr(fl_windows, "restore_if_minimized", lambda: False)
    monkeypatch.setattr(fl_windows, "click_in_window", fake.click)
    monkeypatch.setattr(fl_windows, "press_key_in_window", fake.press_key)
    monkeypatch.setattr(fl_windows, "click_playlist_menu_arrow", fake.click_menu_arrow)
    monkeypatch.setattr(fl_windows, "popup_menu_count", lambda: fake.menus)
    monkeypatch.setattr(fl_windows, "press_in_popup_menu", fake.press)
    monkeypatch.setattr(fl_windows, "pointer_over_menu", lambda: fake.pointer_on_menu)
    monkeypatch.setattr(fl_windows, "close_popup_menus", fake.close_menus)
    return fake


@pytest.fixture
def tools(fl) -> dict[str, Any]:
    collector = ToolCollector()
    register_arrange_tools(collector)
    return collector.tools


EXPECTED = (
    {(1, bar, 1) for bar in (1, 2, 3, 4, 13, 14, 15, 16, 17, 18, 19, 20)}
    | {(2, 5, 3)}
    | {(3, 13, 2), (3, 17, 2)}
)


def test_the_song_is_laid_out_with_one_track_per_pattern(tools, fl):
    fl.pattern_names = {1: "Drums", 2: "Riff", 3: "Chords"}

    result = tools["fl_arrange"](SONG)

    assert fl.clips == EXPECTED
    assert result["total_bars"] == 20 and result["song_length_bars"] == 20
    assert result["tracks"] == [
        {"track": 1, "pattern": 1, "name": "Drums", "clips": 12},
        {"track": 2, "pattern": 3, "name": "Chords", "clips": 1},
        {"track": 3, "pattern": 2, "name": "Riff", "clips": 2},
    ]
    assert [section["start_bar"] for section in result["sections"]] == [1, 5, 13]
    assert fl.track_names == {1: "Drums", 2: "Chords", 3: "Riff"}


def test_fl_is_left_in_song_mode_at_the_start_with_its_own_snap(tools, fl):
    tools["fl_arrange"](SONG)

    assert (fl.mode, fl.position, fl.view_bar) == ("song", 0, 1)
    assert (fl.snap, fl.menus, fl.time_selection) == ("Main", 0, False)


def test_no_click_lands_on_a_clip_while_the_playlist_is_still_scrolling(tools, fl, monkeypatch):
    monkeypatch.setattr(fl_playlist, "SCROLL_TIMEOUT", 1.0)
    fl.scroll_lag = 3

    tools["fl_arrange"](SONG)

    assert fl.clips == EXPECTED
    assert fl.clicks_on_clips == 0


def test_tracks_can_start_further_down(tools, fl):
    result = tools["fl_arrange"]([{"bars": 4, "patterns": [2, 1]}], first_track=9)

    assert [entry["track"] for entry in result["tracks"]] == [9, 10]
    assert fl.clips == {(9, 1, 2)} | {(10, bar, 1) for bar in (1, 2, 3, 4)}


def test_rows_are_found_whatever_their_height(tools, fl):
    fl.row_height = 23

    tools["fl_arrange"](SONG)

    assert fl.clips == EXPECTED


def test_a_playlist_with_a_song_in_it_is_only_replaced_when_asked(tools, fl):
    fl.clips = {(5, 1, 3)}

    refused = tools["fl_arrange"](SONG)

    assert "replace=true" in refused["error"]
    assert fl.clips == {(5, 1, 3)}

    tools["fl_arrange"](SONG, replace=True)

    assert fl.clips == EXPECTED
    assert not fl.time_selection


def test_a_delete_that_goes_unheard_is_tried_again(tools, fl, monkeypatch):
    monkeypatch.setattr(fl_windows, "MENU_FADE_SECONDS", 0.0)
    fl.clips = {(5, 1, 3)}
    fl.deaf_deletes = 1

    tools["fl_arrange"](SONG, replace=True)

    assert fl.clips == EXPECTED


def test_a_playlist_that_cannot_be_emptied_is_left_unselected(tools, fl, monkeypatch):
    monkeypatch.setattr(fl_windows, "MENU_FADE_SECONDS", 0.0)
    fl.clips = {(5, 1, 3)}
    fl.deaf_deletes = 99

    error = tools["fl_arrange"](SONG, replace=True)["error"]

    assert "could not be emptied" in error
    assert fl.clips == {(5, 1, 3)} and not fl.time_selection and fl.menus == 0


def test_names_of_tracks_no_longer_used_are_reset(tools, fl):
    fl.clips = {(7, 1, 3)}
    fl.track_names = {2: "Pattern 2", 7: "Pattern 3", 20: "Vocals"}

    tools["fl_arrange"]([{"bars": 4, "patterns": [1]}], replace=True)

    assert fl.track_names == {1: "Pattern 1", 20: "Vocals"}  # only pattern names go


def test_a_hidden_or_covered_playlist_is_brought_to_the_front_first(tools, fl):
    fl.visible = False

    tools["fl_arrange"](SONG)

    assert fl.visible and fl.in_front and fl.clips == EXPECTED


def test_the_playlist_is_put_at_a_zoom_bars_can_be_aimed_at(tools, fl):
    tools["fl_arrange"](SONG)

    assert fl.zoom == "Quick zoom 2"


def test_the_whole_song_can_be_shown(fl):
    fl_playlist.show_whole_song()

    assert fl.in_front and fl.zoom == "Zoom out far" and fl.menus == 0


def test_a_playlist_that_is_not_in_the_main_window_is_an_error(tools, fl):
    fl.panel = None

    assert "isn't showing" in tools["fl_arrange"](SONG)["error"]


def test_clicks_that_place_nothing_are_noticed(tools, fl):
    fl.press_key = lambda hwnd, key: True  # the draw tool can't be switched on
    fl_windows.press_key_in_window = fl.press_key

    error = tools["fl_arrange"](SONG)["error"]

    assert "should make the song 20 bars long, but it is 0" in error
    assert fl.snap == "Main"


def test_nothing_is_chosen_in_a_menu_under_the_mouse_pointer(tools, fl):
    fl.pointer_on_menu = True

    error = tools["fl_arrange"](SONG)["error"]

    assert "mouse pointer" in error
    assert fl.clips == set() and fl.menus == 0


def test_placing_is_windows_only(tools, fl, monkeypatch):
    monkeypatch.setattr(fl_playlist.platform, "system", lambda: "Darwin")

    assert "only supported on Windows" in tools["fl_arrange"](SONG)["error"]


@pytest.mark.parametrize("kwargs, message", [
    ({"sections": []}, "At least one section"),
    ({"sections": [{"bars": 4, "patterns": []}]}, "nothing to place"),
    ({"sections": [{"bars": 4, "patterns": [3]}]}, "doesn't fit"),
    ({"sections": SONG, "first_track": 0}, "first_track"),
    ({"sections": SONG, "first_track": 499}, "first_track"),
])
def test_bad_arguments_are_refused_before_the_playlist_is_touched(tools, fl, kwargs, message):
    assert message in tools["fl_arrange"](**kwargs)["error"]
    assert fl.sent == ["project.getInfo"]


def test_a_lost_connection_is_reported(tools, fl, monkeypatch):
    def send(action, params=None, timeout=2.0):
        if action == "playlist.getState":
            raise RuntimeError("MIDI port closed")
        return FakePlaylist.send_command(fl, action, params)

    monkeypatch.setattr(fl, "send_command", send)

    assert tools["fl_arrange"](SONG) == {"error": "MIDI port closed"}


def test_a_mute_follows_its_pattern_to_its_new_track(tools, fl):
    # Before: Riff on track 2 (muted), Drums on track 3 (playing), track 1 muted and empty.
    fl.pattern_names = {1: "Drums", 2: "Riff", 3: "Chords"}
    fl.track_names = {2: "Riff", 3: "Drums"}
    fl.muted_tracks = {1, 2, 9}

    tools["fl_arrange"](SONG, replace=True)

    # Now Drums on 1, Chords on 2, Riff on 3; track 9 isn't the arrangement's business.
    assert fl.muted_tracks == {3, 9}


def test_the_playlist_can_be_read(tools, fl):
    fl.clips = {(1, 1, 3)}
    fl.track_names = {1: "Chords"}

    result = tools["fl_get_playlist"](tracks=2)

    assert result == {"visible": True, "length_bars": 8.0, "tracks": [
        {"track": 1, "name": "Chords", "is_muted": False},
        {"track": 2, "name": "Track 2", "is_muted": False},
    ]}


def test_a_playlist_that_never_gets_to_the_bar_is_an_error(tools, fl):
    fl.scroll_lag = 3  # and no time is given for it

    error = tools["fl_arrange"](SONG)["error"]

    assert "did not scroll to bar" in error
    assert fl.snap == "Main"


def test_the_song_position_goes_back_to_the_start_after_a_failure_too(tools, fl):
    fl.scroll_lag = 3  # aiming fails, after ruler clicks have moved the position

    assert "error" in tools["fl_arrange"](SONG)
    assert fl.position == 0
