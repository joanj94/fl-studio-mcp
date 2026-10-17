"""FL-side controller: markers, pattern cloning, meter peaks and group mute/solo."""

from __future__ import annotations

import pytest

TRACK_COUNT = 8  # master, inserts 1-6, FL's "current" track


@pytest.fixture
def mixer(fl_modules):
    """A mixer with mute/solo state and routes {track: [destinations]}."""
    mixer = fl_modules["mixer"]
    state = {"muted": set(), "solo": set(), "routes": {t: [0] for t in range(1, 7)}}
    mixer.trackCount.return_value = TRACK_COUNT
    mixer.isTrackMuted.side_effect = lambda t: int(t in state["muted"])
    mixer.isTrackSolo.side_effect = lambda t: int(t in state["solo"])
    mixer.muteTrack.side_effect = (
        lambda t, on: state["muted"].add(t) if on else state["muted"].discard(t)
    )
    mixer.soloTrack.side_effect = (
        lambda t, on, *mode: state["solo"].add(t) if on else state["solo"].discard(t)
    )
    mixer.getRouteSendActive.side_effect = lambda t, d: d in state["routes"].get(t, [])
    mixer.getTrackPeaks.side_effect = lambda t, mode: 0.1 * t
    return state


def test_add_marker_converts_bars_to_ticks(controller, fl_modules):
    fl_modules["general"].getRecPPB.return_value = 384

    result = controller.dispatch_command("arrangement.addMarker", {"bar": 9, "name": "Drop"})

    assert result == {"bar": 9, "name": "Drop", "ticks": 3072}
    fl_modules["arrangement"].addAutoTimeMarker.assert_called_once_with(3072, "Drop")


@pytest.mark.parametrize("params", [{"bar": 0, "name": "A"}, {"bar": True, "name": "A"},
                                    {"bar": 2, "name": ""}, {"name": "A"}])
def test_add_marker_rejects_bad_input(controller, fl_modules, params):
    assert "error" in controller.dispatch_command("arrangement.addMarker", params)
    fl_modules["arrangement"].addAutoTimeMarker.assert_not_called()


def test_get_markers_reads_names_until_the_first_empty_one(controller, fl_modules):
    names = ["Intro", "Drop"]
    fl_modules["arrangement"].getMarkerName.side_effect = (
        lambda i: names[i] if i < len(names) else ""
    )

    assert controller.dispatch_command("arrangement.getMarkers", {}) == {
        "markers": [{"index": 0, "name": "Intro"}, {"index": 1, "name": "Drop"}]
    }


@pytest.fixture
def pattern_bank(fl_modules):
    used = {1: "Kick"}
    patterns = fl_modules["patterns"]
    patterns.patternMax.return_value = 999
    patterns.patternCount.side_effect = lambda: len(used)
    patterns.patternNumber.return_value = 1
    patterns.isPatternDefault.side_effect = lambda i: i not in used
    patterns.getPatternName.side_effect = lambda i: used.get(i, f"Pattern {i}")
    patterns.getPatternLength.return_value = 16
    return used


def test_clone_pattern_reports_the_new_index(controller, fl_modules, pattern_bank):
    fl_modules["patterns"].clonePattern.side_effect = (
        lambda i: pattern_bank.__setitem__(2, "Pattern 2")
    )

    result = controller.dispatch_command("patterns.clone", {"index": 1})

    assert result == {"index": 2, "source": 1, "name": "Pattern 2"}


def test_clone_refuses_an_empty_pattern(controller, fl_modules, pattern_bank):
    assert "error" in controller.dispatch_command("patterns.clone", {"index": 5})
    fl_modules["patterns"].clonePattern.assert_not_called()


def test_clone_that_adds_nothing_is_an_error(controller, fl_modules, pattern_bank):
    assert "error" in controller.dispatch_command("patterns.clone", {"index": 1})


def test_peaks_of_all_tracks_by_default(controller, mixer):
    result = controller.dispatch_command("mixer.getPeaks", {})

    assert [p["track"] for p in result["peaks"]] == list(range(7))
    assert result["peaks"][3]["peak"] == pytest.approx(0.3)


def test_peaks_refuse_a_track_beyond_the_inserts(controller, mixer):
    assert "error" in controller.dispatch_command("mixer.getPeaks", {"tracks": [7]})


def test_mute_all_except_keeps_sources_and_buses(controller, mixer):
    # 1 and 2 go to bus 5, bus 5 goes to bus 6; 3 and 4 go to the master
    mixer["routes"].update({1: [5], 2: [5], 5: [6]})

    result = controller.dispatch_command("mixer.muteAllExcept", {"tracks": [5]})

    assert result == {"kept": [1, 2, 5, 6], "muted": [3, 4]}
    assert mixer["muted"] == {3, 4}


def test_mute_all_except_needs_insert_tracks(controller, mixer):
    assert "error" in controller.dispatch_command("mixer.muteAllExcept", {"tracks": [0]})
    assert "error" in controller.dispatch_command("mixer.muteAllExcept", {"tracks": []})


def test_clear_mute_solo_takes_solos_off_before_mutes(controller, mixer, fl_modules):
    mixer["muted"].update({1, 2})
    mixer["solo"].add(3)
    calls = []
    fl_modules["mixer"].soloTrack.side_effect = (
        lambda t, on, *mode: (calls.append(("solo", t)), mixer["solo"].discard(t))
    )
    fl_modules["mixer"].muteTrack.side_effect = (
        lambda t, on: (calls.append(("mute", t)), mixer["muted"].discard(t))
    )

    result = controller.dispatch_command("mixer.clearMuteSolo", {})

    assert result == {"unmuted": [1, 2], "unsoloed": [3]}
    assert calls[0] == ("solo", 3)
    assert not mixer["muted"] and not mixer["solo"]


def test_all_tracks_list_their_routes(controller, mixer, fl_modules):
    mixer["routes"][2] = [4]
    fl_modules["mixer"].getTrackName.side_effect = lambda t: f"Track {t}"
    fl_modules["channels"].channelCount.return_value = 0
    fl_modules["plugins"].isValid.return_value = False

    tracks = controller.dispatch_command("mixer.getAllTracks", {})["tracks"]

    routes = {t["index"]: t["routes"] for t in tracks}
    assert routes[0] == [] and routes[1] == [0] and routes[2] == [4]


def test_fls_current_track_after_the_inserts_is_not_a_mixer_track(controller, mixer):
    current = TRACK_COUNT - 1

    result = controller.dispatch_command("mixer.getTrackEffects", {"track": current})

    assert "0-6" in result["error"]
