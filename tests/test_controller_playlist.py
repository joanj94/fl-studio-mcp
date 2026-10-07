"""FL-side controller: the playlist commands the server aims and checks its clicks with."""

from __future__ import annotations

import pytest


@pytest.fixture
def fl(fl_modules):
    """FL modules with a 500-track playlist in 4/4 at 96 PPQ, track 3 selected."""
    fl_modules["general"].getRecPPB.return_value = 384
    playlist = fl_modules["playlist"]
    playlist.trackCount.return_value = 500
    playlist.isTrackSelected.side_effect = lambda index: index == 3
    playlist.isTrackMuted.return_value = 0
    playlist.getTrackName.side_effect = lambda index: f"Track {index}"
    fl_modules["ui"].getVisible.return_value = 1
    return fl_modules


def test_song_length_is_where_the_last_clip_ends(controller, fl):
    # Seen live: a song of 9 bars reads 3455 ticks, an empty playlist 0.
    fl["transport"].getSongLength.return_value = 3455

    state = controller.dispatch_command("playlist.getState", {})

    assert (state["length_ticks"], state["length_bars"], state["bar_ticks"]) == (3456, 9.0, 384)
    assert state["visible"] is True

    fl["transport"].getSongLength.return_value = 0

    assert controller.dispatch_command("playlist.getState", {})["length_bars"] == 0


def test_scrolling_goes_to_a_bar_and_to_a_track(controller, fl):
    controller.dispatch_command("playlist.scrollTo", {"bar": 9, "track": 40})

    window = fl["midi"].widPlaylist
    assert [call.args for call in fl["ui"].scrollWindow.call_args_list] == [
        (window, 9, 1), (window, 40, 0)]


@pytest.mark.parametrize("params", [{"bar": 0}, {"bar": "2"}, {"track": 501}, {"track": True}])
def test_scrolling_refuses_what_is_not_a_bar_or_track(controller, fl, params):
    assert "error" in controller.dispatch_command("playlist.scrollTo", params)
    fl["ui"].scrollWindow.assert_not_called()


def test_the_selected_track_is_reported_within_the_tracks_looked_at(controller, fl):
    assert controller.dispatch_command("playlist.getSelectedTrack", {"upto": 10}) == {
        "selected": [3]}
    assert controller.dispatch_command("playlist.getSelectedTrack", {"upto": 2}) == {
        "selected": []}
    assert "error" in controller.dispatch_command("playlist.getSelectedTrack", {"upto": 0})
    # Looking beyond the last track is looking at all of them.
    assert controller.dispatch_command("playlist.getSelectedTrack", {"upto": 560}) == {
        "selected": [3]}


def test_tracks_are_deselected_named_and_listed(controller, fl):
    controller.dispatch_command("playlist.deselectTracks", {})
    fl["playlist"].deselectAll.assert_called_once_with()

    controller.dispatch_command("playlist.setTrackName", {"track": 2, "name": "Kick"})
    fl["playlist"].setTrackName.assert_called_once_with(2, "Kick")
    assert "error" in controller.dispatch_command("playlist.setTrackName", {"track": 0})

    assert controller.dispatch_command("playlist.getTracks", {"count": 2})["tracks"] == [
        {"track": 1, "name": "Track 1", "is_muted": False},
        {"track": 2, "name": "Track 2", "is_muted": False},
    ]


def test_a_track_is_muted_and_unmuted_and_read_back(controller, fl):
    fl["playlist"].isTrackMuted.return_value = 1

    result = controller.dispatch_command("playlist.setTrackMuted", {"track": 3, "muted": True})

    fl["playlist"].muteTrack.assert_called_once_with(3, 1)
    assert result == {"track": 3, "is_muted": True}
    controller.dispatch_command("playlist.setTrackMuted", {"track": 3, "muted": False})
    fl["playlist"].muteTrack.assert_called_with(3, 0)
    assert "error" in controller.dispatch_command("playlist.setTrackMuted", {"track": 0})
    assert "error" in controller.dispatch_command(
        "playlist.setTrackMuted", {"track": 3, "muted": "yes"})


def test_the_song_position_is_read_in_ticks(controller, fl):
    fl["transport"].getSongPos.return_value = 768

    assert controller.dispatch_command("playlist.getSongPosition", {}) == {"ticks": 768}
    fl["transport"].getSongPos.assert_called_once_with(fl["midi"].SONGLENGTH_ABSTICKS)
