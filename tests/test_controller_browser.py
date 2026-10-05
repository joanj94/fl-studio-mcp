"""FL-side controller: browser, UI keys, mixer effect slots, undo and preset names."""

from __future__ import annotations

import pytest


@pytest.fixture
def browser(controller, fl_modules):
    """A browser list of four items; the cursor can't leave it."""
    rows = [("Packs", -100), ("Kick.wav", 7), ("Kick.wav", 7), ("Snare.wav", 7)]
    state = {"at": 0}
    ui = fl_modules["ui"]
    up = controller.BROWSER_DIRECTIONS["previous"]

    def navigate(direction, shift):
        step = -1 if direction == up else 1
        state["at"] = max(0, min(len(rows) - 1, state["at"] + step))

    ui.navigateBrowser.side_effect = navigate
    ui.getFocusedNodeCaption.side_effect = lambda: rows[state["at"]][0]
    ui.getFocusedNodeFileType.side_effect = lambda: rows[state["at"]][1]
    return state


def test_an_item_seen_again_at_the_last_step_is_left_undecided(controller, browser):
    result = controller.dispatch_command("browser.navigate", {"direction": "next", "steps": 2})

    # two files share a name: the second may be a neighbour or the end of the list
    kick = {"name": "Kick.wav", "file_type": 7}
    assert result == {"items": [kick], "found": False, "stuck": False, "pending": [kick]}
    assert browser["at"] == 2  # exactly two steps were taken


def test_neighbours_alike_are_both_counted_once_the_cursor_moves_on(controller, browser):
    result = controller.dispatch_command("browser.navigate", {"direction": "next", "steps": 50})

    assert [item["name"] for item in result["items"]] == ["Kick.wav", "Kick.wav", "Snare.wav"]
    assert result["stuck"] is True and result["pending"] == []


def test_navigate_stops_at_an_item_of_that_name_and_type(controller, browser):
    wrong_type = controller.dispatch_command(
        "browser.navigate", {"direction": "next", "steps": 50, "until": "Kick.wav",
                             "until_type": -100})
    assert wrong_type["found"] is False

    browser["at"] = 0
    result = controller.dispatch_command(
        "browser.navigate", {"direction": "next", "steps": 50, "until": "Kick.wav",
                             "until_type": 7})

    assert result["found"] is True and len(result["items"]) == 1
    assert browser["at"] == 1


def test_a_long_walk_answers_before_the_servers_timeout(controller, browser, fl_modules,
                                                         monkeypatch):
    clock = iter(range(0, 1000, 2))  # every look at the clock is two seconds later
    monkeypatch.setattr(controller.time, "time", lambda: next(clock))

    result = controller.dispatch_command("browser.navigate", {"direction": "next", "steps": 50})

    assert len(result["items"]) == 1 and result["stuck"] is False


def test_navigate_closes_the_folders_it_reaches_when_asked(controller, browser, fl_modules):
    browser["at"] = 2

    controller.dispatch_command("browser.navigate", {"direction": "previous", "steps": 50,
                                                     "collapse": True})

    # for "Packs" only, each time the cursor was on it (it stays there at the top)
    calls = fl_modules["ui"].toggleBrowserNode.call_args_list
    assert calls and all(call.args == (0,) for call in calls)
    assert browser["at"] == 0


@pytest.mark.parametrize("params", [
    {"direction": "sideways"}, {"steps": 0}, {"steps": 501}, {"steps": "many"}, {"steps": True},
])
def test_navigate_rejects_bad_requests(controller, browser, params):
    assert "error" in controller.dispatch_command("browser.navigate", params)
    assert browser["at"] == 0


def test_focused_toggle_and_open_report_the_focused_item(controller, browser, fl_modules):
    ui = fl_modules["ui"]
    packs = {"name": "Packs", "file_type": -100}

    assert controller.dispatch_command("browser.getFocused", {}) == packs
    assert controller.dispatch_command("browser.toggleNode", {"value": 1}) == packs
    ui.toggleBrowserNode.assert_called_once_with(1)
    assert controller.dispatch_command("browser.openFocused", {}) == packs
    ui.selectBrowserMenuItem.assert_called_once_with()


def test_select_tab_returns_the_tab_now_showing(controller, fl_modules):
    fl_modules["ui"].navigateBrowserTabs.return_value = "ALL"

    assert controller.dispatch_command("browser.selectTab", {"direction": "first"}) == {
        "tab": "ALL"}
    assert "error" in controller.dispatch_command("browser.selectTab", {"direction": "up"})


@pytest.fixture
def ui_state(fl_modules):
    ui = fl_modules["ui"]
    ui.getFocusedFormCaption.return_value = "Browser"
    ui.getFocusedFormID.return_value = 4
    ui.isInPopupMenu.return_value = 1
    return {"focused_window": "Browser", "focused_window_id": 4, "in_popup_menu": True}


def test_focus_window_shows_and_focuses_it(controller, fl_modules, ui_state):
    ui = fl_modules["ui"]
    window = controller.UI_WINDOWS["browser"]

    assert controller.dispatch_command("ui.focusWindow", {"window": "browser"}) == ui_state
    ui.showWindow.assert_called_once_with(window)
    ui.setFocused.assert_called_once_with(window)
    assert "error" in controller.dispatch_command("ui.focusWindow", {"window": "toolbar"})


MUTE, MIX = 7, 8  # stand-ins for FL's event offsets
PLUGIN_ID = {slot: 1000 * (slot + 1) for slot in range(10)}


@pytest.fixture
def mixer_track(fl_modules):
    fl_modules["mixer"].trackCount.return_value = 127
    fl_modules["mixer"].getTrackName.return_value = "Kick"
    plugins = fl_modules["plugins"]
    plugins.isValid.side_effect = lambda track, slot, use_global: slot in (0, 9)
    plugins.getPluginName.side_effect = lambda track, slot, user, use_global: f"Effect {slot}"
    midi, mixer = fl_modules["midi"], fl_modules["mixer"]
    midi.REC_Plug_Mute, midi.REC_Plug_MixLevel = MUTE, MIX
    midi.REC_Control, midi.REC_UpdateControl = 1, 16
    mixer.getTrackPluginId.side_effect = lambda track, slot: PLUGIN_ID[slot]
    values = {PLUGIN_ID[0] + MUTE: 1, PLUGIN_ID[0] + MIX: 1 << 30,
              PLUGIN_ID[9] + MUTE: 0, PLUGIN_ID[9] + MIX: 1 << 29}
    mixer.getEventValue.side_effect = lambda event: values[event]
    return fl_modules


def test_track_effects_lists_the_filled_slots(controller, mixer_track):
    assert controller.dispatch_command("mixer.getTrackEffects", {"track": 3}) == {
        "track": 3, "name": "Kick",
        "effects": [
            {"slot": 0, "plugin": "Effect 0", "enabled": True, "mix": 1.0},
            {"slot": 9, "plugin": "Effect 9", "enabled": False, "mix": 0.5},
        ],
    }


def test_set_effect_switches_it_and_sets_its_mix_on_fls_scale(controller, mixer_track):
    midi, general = mixer_track["midi"], mixer_track["general"]
    flags = midi.REC_Control | midi.REC_UpdateControl

    result = controller.dispatch_command(
        "mixer.setEffect", {"track": 3, "slot": 9, "enabled": False, "mix": 0.25})

    assert result["track"] == 3 and len(result["effects"]) == 2
    assert general.processRECEvent.call_args_list == [
        ((PLUGIN_ID[9] + MUTE, 0, flags),), ((PLUGIN_ID[9] + MIX, 3200, flags),),
    ]


def test_set_effect_leaves_alone_what_was_not_given(controller, mixer_track):
    controller.dispatch_command("mixer.setEffect", {"track": 3, "slot": 0, "enabled": True})

    assert mixer_track["general"].processRECEvent.call_count == 1


@pytest.mark.parametrize("params", [
    {"track": 3, "slot": 4, "enabled": False},  # nothing in that slot
    {"track": 3, "slot": 10, "enabled": False},
    {"track": 3, "slot": 0, "mix": 1.5},
    {"track": 3, "slot": 0, "mix": "half"},
    {"track": 200, "slot": 0, "mix": 0.5},
    {"track": 3, "slot": 0, "enabled": "false"},  # a string would count as on
    {"track": 3, "slot": 0},  # nothing to change
])
def test_set_effect_rejects_bad_requests(controller, mixer_track, params):
    assert "error" in controller.dispatch_command("mixer.setEffect", params)
    mixer_track["general"].processRECEvent.assert_not_called()


def test_routing_beyond_the_projects_last_insert_is_refused(controller, fl_modules):
    fl_modules["mixer"].trackCount.return_value = 18  # master, 16 inserts, "current"
    channels = fl_modules["channels"]
    channels.channelCount.return_value = 8
    channels.getChannelName.return_value = "Kick"
    channels.getTargetFxTrack.return_value = 16

    for channel in (8, -1, "5", None):
        result = controller.dispatch_command("channels.routeToMixer",
                                             {"channel_index": channel, "mixer_track": 3})
        assert "channel_index must be 0-7" in result["error"]
    channels.setTargetFxTrack.assert_not_called()

    ok = controller.dispatch_command("channels.routeToMixer",
                                     {"channel_index": 5, "mixer_track": 16})
    assert ok == {"channel_name": "Kick", "mixer_track": 16}

    for track in (17, 40, -1, "3", True):
        result = controller.dispatch_command("channels.routeToMixer",
                                             {"channel_index": 5, "mixer_track": track})
        assert "must be 0 (master) to 16" in result["error"]
    assert channels.setTargetFxTrack.call_count == 1


@pytest.mark.parametrize("track", [-1, 127, "1", None, True])
def test_mixer_track_numbers_are_checked(controller, mixer_track, track):
    assert "error" in controller.dispatch_command("mixer.getTrackEffects", {"track": track})


def test_undo_steps_back_in_the_history(controller, fl_modules):
    general = fl_modules["general"]
    general.getUndoHistoryPos.return_value = 2
    general.getUndoHistoryCount.return_value = 5

    assert controller.dispatch_command("project.undo", {"steps": 2}) == {
        "undone": 2, "history_position": 2, "history_count": 5}
    assert general.undoUp.call_count == 2


@pytest.mark.parametrize("steps", [0, 51, "1", True])
def test_undo_rejects_bad_step_counts(controller, fl_modules, steps):
    assert "error" in controller.dispatch_command("project.undo", {"steps": steps})
    fl_modules["general"].undoUp.assert_not_called()


def test_list_presets_reads_each_preset_name(controller, fl_modules):
    plugins, midi = fl_modules["plugins"], fl_modules["midi"]
    plugins.getPresetCount.return_value = 2
    plugins.getName.side_effect = lambda index, slot, flag, preset, use_global: f"Preset {preset}"

    assert controller.dispatch_command("plugins.listPresets", {"index": 4}) == {
        "count": 2, "presets": ["Preset 0", "Preset 1"]}
    plugins.getName.assert_called_with(4, -1, midi.FPN_Preset, 1, True)


def test_a_track_level_in_db_is_found_by_moving_the_fader(controller, fl_modules):
    mixer = fl_modules["mixer"]
    mixer.trackCount.return_value = 18
    fader = {"at": 0.8}
    mixer.setTrackVolume.side_effect = lambda track, volume: fader.update(at=volume)
    # a stand-in for FL's fader law: 0.8 is 0 dB, and it rises with the position
    mixer.getTrackVolume.side_effect = lambda track, mode=0: (
        (fader["at"] - 0.8) * 40 if mode else fader["at"])

    result = controller.dispatch_command("mixer.setTrackVolumeDb", {"track": 6, "db": -6.0})

    assert result["track"] == 6
    assert result["volume_db"] == pytest.approx(-6.0, abs=0.001)
    assert result["volume"] == pytest.approx(0.65, abs=0.001)


def test_the_master_level_is_found_on_an_insert_since_its_own_reading_lags(controller, fl_modules):
    mixer = fl_modules["mixer"]
    mixer.trackCount.return_value = 18
    faders = {0: 0.8, 1: 0.7}
    shown = dict(faders)  # what FL answers: the master's only changes after the command

    def move(track, volume):
        faders[track] = volume
        if track != 0:
            shown[track] = volume

    mixer.setTrackVolume.side_effect = move
    mixer.getTrackVolume.side_effect = lambda track, mode=0: (
        (shown[track] - 0.8) * 40 if mode else shown[track])

    result = controller.dispatch_command("mixer.setTrackVolumeDb", {"track": 0, "db": -6.0})

    assert faders[0] == pytest.approx(0.65, abs=0.001)
    assert faders[1] == 0.7
    assert result["track"] == 0
    assert result["volume_db"] == pytest.approx(-6.0, abs=0.001)
    assert result["volume"] == pytest.approx(0.65, abs=0.001)


@pytest.mark.parametrize("params", [
    {"track": 6, "db": 6.0}, {"track": 6, "db": -200}, {"track": 6, "db": "loud"},
    {"track": 6}, {"track": 99, "db": -6.0},
])
def test_a_track_level_out_of_range_is_refused(controller, fl_modules, params):
    fl_modules["mixer"].trackCount.return_value = 18

    assert "error" in controller.dispatch_command("mixer.setTrackVolumeDb", params)
    fl_modules["mixer"].setTrackVolume.assert_not_called()


def _faders(mixer, positions, lagging=()):
    """A mixer whose `lagging` tracks answer with the position they had before the command."""
    mixer.trackCount.return_value = 18
    faders = {t: positions.get(t, 0.8) for t in range(18)}
    shown = dict(faders)

    def move(track, volume):
        faders[track] = volume
        if track not in lagging:
            shown[track] = volume

    mixer.setTrackVolume.side_effect = move
    mixer.getTrackVolume.side_effect = lambda track, mode=0: (
        (shown[track] - 0.8) * 40 if mode else shown[track])
    return faders


def test_an_insert_whose_reading_lags_gets_its_level_found_on_another(controller, fl_modules):
    # seen live: an insert carrying five effects read one stale value, and the
    # search left its fader at -76 dB
    faders = _faders(fl_modules["mixer"], {1: 0.7, 15: 0.8}, lagging={15})

    result = controller.dispatch_command("mixer.setTrackVolumeDb", {"track": 15, "db": -4.0})

    assert faders[15] == pytest.approx(0.7, abs=0.001)
    assert faders[1] == 0.7
    assert result["volume_db"] == pytest.approx(-4.0, abs=0.001)


def test_a_level_is_refused_when_no_reading_follows_its_fader(controller, fl_modules):
    faders = _faders(fl_modules["mixer"], {6: 0.6}, lagging=set(range(18)))

    result = controller.dispatch_command("mixer.setTrackVolumeDb", {"track": 6, "db": -4.0})

    assert "error" in result
    assert faders[6] == 0.6
    assert all(faders[t] == 0.8 for t in range(18) if t != 6)


def test_a_failed_level_search_puts_the_fader_back(controller, fl_modules):
    mixer = fl_modules["mixer"]
    faders = _faders(mixer, {6: 0.7})
    law = mixer.getTrackVolume.side_effect
    readings = {"n": 0}

    def read(track, mode=0):
        if mode:
            readings["n"] += 1
            if readings["n"] > len(controller.FADER_CHECK_POSITIONS):  # the search, not the check
                raise RuntimeError("FL is busy")
        return law(track, mode)

    mixer.getTrackVolume.side_effect = read

    with pytest.raises(RuntimeError):
        controller.handle_mixer_set_track_volume_db({"track": 6, "db": -6.0})
    assert faders[6] == 0.7


@pytest.fixture
def routes(fl_modules):
    """Mixer of 16 inserts whose routes are kept as (source, destination) pairs."""
    mixer = fl_modules["mixer"]
    mixer.trackCount.return_value = 18  # master, 16 inserts, "current"
    sends = {(t, 0) for t in range(1, 17)}

    def route(track, dest, on, *rest):
        (sends.add if on else sends.discard)((track, dest))

    mixer.setRouteTo.side_effect = route
    mixer.getRouteSendActive.side_effect = lambda track, dest: (track, dest) in sends
    levels = {}
    mixer.setRouteToLevel.side_effect = lambda track, dest, level: levels.update(
        {(track, dest): level})
    mixer.getRouteToLevel.side_effect = lambda track, dest: levels.get((track, dest), 0.8)
    return sends


def test_a_track_routed_to_a_bus_leaves_the_master(controller, routes):
    result = controller.dispatch_command("mixer.routeTrack", {"track": 2, "to": 16})

    assert result == {"track": 2, "to": 16, "routes": [16]}
    assert (2, 16) in routes and (2, 0) not in routes


def test_routing_back_to_the_master_leaves_the_bus(controller, routes):
    controller.dispatch_command("mixer.routeTrack", {"track": 2, "to": 16})

    result = controller.dispatch_command("mixer.routeTrack", {"track": 2, "to": 0})

    assert result == {"track": 2, "to": 0, "routes": [0]}
    assert (2, 0) in routes and (2, 16) not in routes


@pytest.mark.parametrize("track, to", [(0, 3), (17, 0), (3, 3), (3, 18)])
def test_the_master_the_current_track_and_loops_are_refused(controller, routes, track, to):
    result = controller.dispatch_command("mixer.routeTrack", {"track": track, "to": to})

    assert "error" in result
    assert (3, 3) not in routes


def test_a_send_keeps_the_other_routes_and_sets_its_level(controller, routes):
    controller.dispatch_command("mixer.routeTrack", {"track": 1, "to": 16})

    result = controller.dispatch_command(
        "mixer.routeTrack", {"track": 1, "to": 2, "only": False, "level": 0.0})

    assert result == {"track": 1, "to": 2, "routes": [2, 16], "level": 0.0}
    assert {(1, 2), (1, 16)} <= routes


@pytest.mark.parametrize("level", [-0.1, 1.5, True, "loud"])
def test_a_level_outside_0_to_1_is_refused(controller, routes, level):
    result = controller.dispatch_command(
        "mixer.routeTrack", {"track": 1, "to": 2, "only": False, "level": level})

    assert "error" in result
    assert (1, 2) not in routes
