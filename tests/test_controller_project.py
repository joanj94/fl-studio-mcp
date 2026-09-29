"""FL-side controller: project (tempo, overview), pattern and step parameter handlers."""

from __future__ import annotations

import pytest

MAX_PATTERNS = 999


@pytest.fixture
def fl(fl_modules):
    """FL modules with a small project: patterns 1 and 3 used, pattern 1 active, 96 PPQ, 4/4."""
    used = {1: ("Intro", 16), 3: ("Drop", 32)}
    names = dict((i, name) for i, (name, _) in used.items())

    patterns = fl_modules["patterns"]
    patterns.patternMax.return_value = MAX_PATTERNS
    patterns.patternCount.return_value = len(used)
    patterns.patternNumber.return_value = 1
    patterns.isPatternDefault.side_effect = lambda i: i not in used
    patterns.getPatternName.side_effect = lambda i: names.get(i, f"Pattern {i}")
    patterns.getPatternLength.side_effect = lambda i: used.get(i, ("", 16))[1]
    patterns.setPatternName.side_effect = lambda i, name: names.__setitem__(i, name)

    general = fl_modules["general"]
    general.getRecPPQ.return_value = 96
    general.getRecPPB.return_value = 384

    fl_modules["mixer"].getCurrentTempo.return_value = 150000
    fl_modules["mixer"].trackCount.return_value = 18
    fl_modules["channels"].channelCount.return_value = 1
    fl_modules["channels"].getChannelName.return_value = "Kick"
    fl_modules["channels"].getTargetFxTrack.return_value = 1
    fl_modules["channels"].isChannelMuted.return_value = 0
    fl_modules["channels"].isChannelSelected.return_value = 1
    fl_modules["transport"].getLoopMode.return_value = 0
    return fl_modules


def test_get_tempo_converts_fl_thousandths(controller, fl):
    # Seen live in FL 2026 (opposite of the API docs): getCurrentTempo() returns
    # thousandths of a BPM (150500), getCurrentTempo(True) whole BPM (150).
    fl["mixer"].getCurrentTempo.return_value = 150500

    assert controller.dispatch_command("project.getTempo", {}) == {"bpm": 150.5}
    fl["mixer"].getCurrentTempo.assert_called_once_with()


def test_set_tempo_sends_rec_event_in_thousandths(controller, fl):
    midi, general = fl["midi"], fl["general"]

    controller.dispatch_command("project.setTempo", {"bpm": 174.5})

    general.processRECEvent.assert_called_once_with(
        midi.REC_Tempo, 174500, midi.REC_Control | midi.REC_UpdateControl
    )


@pytest.mark.parametrize("bpm", [5, 600, "fast", None])
def test_set_tempo_rejects_out_of_range_values(controller, fl, bpm):
    result = controller.dispatch_command("project.setTempo", {"bpm": bpm})

    assert "error" in result
    fl["general"].processRECEvent.assert_not_called()


def test_get_all_patterns_lists_only_used_ones(controller, fl):
    result = controller.dispatch_command("patterns.getAll", {})

    assert result == {
        "current": 1,
        "patterns": [
            {"index": 1, "name": "Intro", "length_beats": 16, "is_current": True},
            {"index": 3, "name": "Drop", "length_beats": 32, "is_current": False},
        ],
    }


def test_pattern_scan_stops_once_all_used_patterns_are_found(controller, fl):
    controller.dispatch_command("patterns.getAll", {})

    assert fl["patterns"].isPatternDefault.call_count == 3


def test_select_pattern_jumps_to_it(controller, fl):
    result = controller.dispatch_command("patterns.select", {"index": 3})

    fl["patterns"].jumpToPattern.assert_called_once_with(3)
    assert result["name"] == "Drop"


@pytest.mark.parametrize("index", [0, MAX_PATTERNS + 1, "2", None])
def test_select_pattern_rejects_invalid_index(controller, fl, index):
    result = controller.dispatch_command("patterns.select", {"index": index})

    assert "error" in result
    fl["patterns"].jumpToPattern.assert_not_called()


def test_rename_pattern(controller, fl):
    result = controller.dispatch_command("patterns.rename", {"index": 3, "name": "Break"})

    fl["patterns"].setPatternName.assert_called_once_with(3, "Break")
    assert result == {"index": 3, "name": "Break"}


def test_rename_defaults_to_current_pattern(controller, fl):
    controller.dispatch_command("patterns.rename", {"name": "Main"})

    fl["patterns"].setPatternName.assert_called_once_with(1, "Main")


def test_find_empty_pattern_returns_first_unused_index(controller, fl):
    assert controller.dispatch_command("patterns.findEmpty", {}) == {"index": 2}
    fl["patterns"].jumpToPattern.assert_not_called()


def test_find_empty_pattern_when_all_are_used(controller, fl):
    fl["patterns"].isPatternDefault.side_effect = lambda i: False

    assert "error" in controller.dispatch_command("patterns.findEmpty", {})


def test_project_info_summarizes_the_project(controller, fl):
    info = controller.dispatch_command("project.getInfo", {})

    assert info["bpm"] == 150.0
    assert info["ppq"] == 96
    assert info["beats_per_bar"] == 4
    assert info["current_pattern"] == 1
    assert [p["index"] for p in info["patterns"]] == [1, 3]
    assert info["channels"][0]["name"] == "Kick"
    assert info["mixer_track_count"] == 18
    assert info["loop_mode"] == "pattern"


def test_set_step_params_writes_each_param_and_grid_bit(controller, fl):
    channels = fl["channels"]

    result = controller.dispatch_command("channels.setStepParams", {
        "channel": 2,
        "steps": [{"step": 0, "on": True, "pitch": 48, "velocity": 127},
                  {"step": 4, "on": False}],
    })

    channels.setGridBit.assert_any_call(2, 0, 1, True)
    channels.setGridBit.assert_any_call(2, 4, 0, True)
    # pitch = 0, velocity = 1 (FL step parameter ids); pattern 1 is active
    channels.setStepParameterByIndex.assert_any_call(2, 1, 0, 0, 48, True)
    channels.setStepParameterByIndex.assert_any_call(2, 1, 0, 1, 127, True)
    assert channels.setStepParameterByIndex.call_count == 2
    assert result["steps_written"] == 2
    assert result["params_written"] == 2


def test_set_step_params_counts_steps_switched_off_without_params(controller, fl):
    result = controller.dispatch_command("channels.setStepParams", {
        "channel": 2, "steps": [{"step": 0, "on": False}, {"step": 2, "on": False}],
    })

    assert result["steps_written"] == 2
    assert result["params_written"] == 0


def test_set_step_params_in_another_pattern_selects_it_first(controller, fl):
    controller.dispatch_command("channels.setStepParams", {
        "channel": 0, "pattern": 3, "steps": [{"step": 1, "velocity": 90}],
    })

    fl["patterns"].jumpToPattern.assert_called_once_with(3)


def test_set_step_params_rejects_unknown_param_before_writing(controller, fl):
    result = controller.dispatch_command("channels.setStepParams", {
        "channel": 0, "steps": [{"step": 0, "velocity": 90}, {"step": 1, "wobble": 3}],
    })

    assert "wobble" in result["error"]
    fl["channels"].setStepParameterByIndex.assert_not_called()


def test_get_step_params_reads_every_param_for_active_steps(controller, fl):
    channels = fl["channels"]
    channels.getGridBit.side_effect = lambda channel, step, use_global: int(step == 1)
    channels.getCurrentStepParam.side_effect = lambda channel, step, param, use_global: param

    result = controller.dispatch_command("channels.getStepParams", {"channel": 0, "steps": 2})

    assert result["steps"][0] == {"step": 0, "on": False}
    assert result["steps"][1] == {
        "step": 1, "on": True, "pitch": 0, "velocity": 1, "release": 2, "fine_pitch": 3,
        "pan": 4, "mod_x": 5, "mod_y": 6, "shift": 7 - 24,
    }


def test_step_shift_is_relative_to_the_step_position(controller, fl):
    # Seen live: FL reports shift as the step's absolute tick position (step * PPQ/4 + shift).
    channels = fl["channels"]
    channels.getGridBit.return_value = 1
    channels.getCurrentStepParam.side_effect = (
        lambda channel, step, param, use_global: step * 24 + 6 if param == 7 else 0
    )

    result = controller.dispatch_command("channels.getStepParams", {"channel": 0, "steps": 3})

    assert [s["shift"] for s in result["steps"]] == [6, 6, 6]


def test_step_shift_is_written_as_absolute_position(controller, fl):
    controller.dispatch_command("channels.setStepParams", {
        "channel": 0, "steps": [{"step": 2, "shift": 6}],
    })

    fl["channels"].setStepParameterByIndex.assert_called_once_with(0, 1, 2, 7, 2 * 24 + 6, True)


def test_get_all_channels_includes_plugin_name(controller, fl_modules):
    fl_modules["channels"].channelCount.return_value = 2
    fl_modules["channels"].getChannelName.side_effect = ["Kick", "Lead"]
    fl_modules["plugins"].isValid.side_effect = [False, True]
    fl_modules["plugins"].getPluginName.return_value = "Sytrus"

    result = controller.dispatch_command("channels.getAll", {})

    assert [c["plugin"] for c in result["channels"]] == [None, "Sytrus"]
    # getPluginName(index, slotIndex, userName, useGlobalIndex): the plugin's own name
    fl_modules["plugins"].getPluginName.assert_called_once_with(1, -1, False, True)


def test_show_in_piano_roll_selects_then_reopens_the_window(controller, fl_modules):
    calls = []
    fl_modules["channels"].channelCount.return_value = 5
    fl_modules["channels"].getChannelName.return_value = "808 Astronomic"
    fl_modules["channels"].selectOneChannel.side_effect = lambda i, g: calls.append(f"select {i}")
    fl_modules["ui"].hideWindow.side_effect = lambda w: calls.append("hide")
    fl_modules["ui"].showWindow.side_effect = lambda w: calls.append("show")

    result = controller.dispatch_command("channels.showInPianoRoll", {"index": 4})

    assert calls == ["select 4", "hide", "show"]
    fl_modules["ui"].showWindow.assert_called_with(fl_modules["midi"].widPianoRoll)
    assert result == {"channel_name": "808 Astronomic"}


@pytest.mark.parametrize("index", [-1, 5, "4", None])
def test_show_in_piano_roll_rejects_bad_indexes(controller, fl_modules, index):
    fl_modules["channels"].channelCount.return_value = 5

    result = controller.dispatch_command("channels.showInPianoRoll", {"index": index})

    assert "error" in result
    fl_modules["channels"].selectOneChannel.assert_not_called()
    fl_modules["ui"].hideWindow.assert_not_called()


def test_reload_script_re_executes_the_installed_file(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "SCRIPT_DIR", tmp_path)
    (tmp_path / "device_FLStudioMCP.py").write_text("RELOAD_MARKER = 42\n")

    result = controller.dispatch_command("dev.reloadScript", {})

    assert result == {"reloaded": str(tmp_path / "device_FLStudioMCP.py")}
    assert controller.RELOAD_MARKER == 42


def test_reload_script_reports_a_broken_file_without_changing_anything(
    controller, tmp_path, monkeypatch
):
    monkeypatch.setattr(controller, "SCRIPT_DIR", tmp_path)
    (tmp_path / "device_FLStudioMCP.py").write_text("RELOAD_MARKER = (\n")

    result = controller.dispatch_command("dev.reloadScript", {})

    assert "SyntaxError" in result["error"]
    assert not hasattr(controller, "RELOAD_MARKER")


def test_reload_script_reports_a_runtime_error_as_partial(controller, tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "SCRIPT_DIR", tmp_path)
    (tmp_path / "device_FLStudioMCP.py").write_text(
        "RELOAD_MARKER = 1\nraise RuntimeError('boom')\n"
    )

    result = controller.dispatch_command("dev.reloadScript", {})

    assert "boom" in result["error"]
    assert "partly" in result["error"]


def test_get_all_channels_survives_a_failing_plugin_lookup(controller, fl_modules):
    fl_modules["channels"].channelCount.return_value = 2
    fl_modules["plugins"].isValid.side_effect = [RuntimeError("broken plugin"), True]
    fl_modules["plugins"].getPluginName.return_value = "Sytrus"

    result = controller.dispatch_command("channels.getAll", {})

    assert [c["plugin"] for c in result["channels"]] == [None, "Sytrus"]


@pytest.mark.parametrize("action", ["plugins.getName", "plugins.nextPreset", "plugins.prevPreset"])
@pytest.mark.parametrize("params, expected", [
    ({"index": 3}, (3, -1, False, True)),  # channel plugin, global index
    ({"index": 3, "slot_index": 2}, (3, 2, False)),  # mixer effect slot
])
def test_plugin_handlers_ask_for_the_plugin_name_not_the_user_name(
    controller, fl_modules, action, params, expected
):
    fl_modules["plugins"].isValid.return_value = True
    fl_modules["plugins"].getParamCount.return_value = 0

    controller.dispatch_command(action, params)

    fl_modules["plugins"].getPluginName.assert_called_with(*expected)
