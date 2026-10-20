"""FL-side controller: choosing a plugin's preset by name, drum pads, pattern colours."""

from __future__ import annotations

import pytest

PRESETS = ["808 Astronomic", "808 Avalanche", "808 Behemoth", "808 Beneath", "Bass Lead"]


@pytest.fixture
def presets(fl_modules):
    plugins, midi = fl_modules["plugins"], fl_modules["midi"]
    midi.FPN_Preset = 6
    midi.FPN_Semitone = 2
    midi.GPN_GetCurrentPreset = -1
    state = {"current": 0, "steps": 0}
    plugins.getPresetCount.return_value = len(PRESETS)
    plugins.isValid.return_value = True

    def name(index, slot, kind, which, *rest):
        return PRESETS[state["current"] if which == -1 else which]

    def step(delta):
        state["current"] = (state["current"] + delta) % len(PRESETS)
        state["steps"] += 1

    plugins.getName.side_effect = name
    plugins.nextPreset.side_effect = lambda *a: step(1)
    plugins.prevPreset.side_effect = lambda *a: step(-1)
    return state


def test_select_preset_steps_the_shorter_way(controller, presets):
    result = controller.dispatch_command("plugins.selectPreset",
                                         {"index": 4, "name": "bass lead"})

    assert result == {"index": 4, "preset": "Bass Lead", "steps": -1}
    assert presets["steps"] == 1  # one back (wrapping) instead of four forward


def test_select_preset_by_a_unique_part_of_the_name(controller, presets):
    assert controller.dispatch_command(
        "plugins.selectPreset", {"index": 4, "name": "behemoth"})["preset"] == "808 Behemoth"


@pytest.mark.parametrize("name", ["808", "nothing like it", ""])
def test_select_preset_refuses_unknown_or_ambiguous_names(controller, presets, name):
    result = controller.dispatch_command("plugins.selectPreset", {"index": 4, "name": name})

    assert "error" in result
    assert presets["steps"] == 0


def test_current_preset_is_read_in_a_command_of_its_own(controller, presets):
    controller.dispatch_command("plugins.selectPreset", {"index": 4, "name": "Behemoth"})

    assert controller.dispatch_command("plugins.getCurrentPreset", {"index": 4}) == {
        "index": 4, "preset": "808 Behemoth"}


def test_pads_list_note_colour_and_name(controller, presets, fl_modules):
    plugins = fl_modules["plugins"]
    pads = {0: (36, 0x0000FF, "Kick"), 1: (38, 0x00FF00, "")}

    def pad_info(channel, slot, option, pad=-1, *rest):
        if option == 0:
            return len(pads)
        return pads[pad][0] if option == 1 else pads[pad][1]

    plugins.getPadInfo.side_effect = pad_info
    plugins.getName.side_effect = lambda i, s, kind, note, *r: {36: "Kick", 38: ""}[note]

    result = controller.dispatch_command("plugins.getPads", {"index": 5})

    assert result["pads"] == [
        {"pad": 0, "note": 36, "color": "#FF0000", "name": "Kick"},
        {"pad": 1, "note": 38, "color": "#00FF00"},
    ]


def test_pattern_colour_is_packed_and_read_back(controller, fl_modules):
    patterns = fl_modules["patterns"]
    patterns.patternMax.return_value = 999
    colours = {}
    patterns.setPatternColor.side_effect = lambda i, c: colours.update({i: c})
    patterns.getPatternColor.side_effect = lambda i: colours.get(i, 0)

    result = controller.dispatch_command("patterns.setColor",
                                         {"index": 3, "red": 255, "green": 128, "blue": 0})

    assert colours[3] == 0xFF8000
    assert result == {"index": 3, "color": "#FF8000"}


@pytest.mark.parametrize("params", [{"index": 0, "red": 1, "green": 1, "blue": 1},
                                    {"index": 3, "red": 256, "green": 1, "blue": 1},
                                    {"index": 3, "red": 1, "green": 1}])
def test_pattern_colour_refuses_bad_values(controller, fl_modules, params):
    fl_modules["patterns"].patternMax.return_value = 999

    assert "error" in controller.dispatch_command("patterns.setColor", params)
    fl_modules["patterns"].setPatternColor.assert_not_called()
