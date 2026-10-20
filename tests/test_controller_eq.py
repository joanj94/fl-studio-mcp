"""FL-side controller: the mixer's built-in EQ, the time signature, redo and undo history."""

from __future__ import annotations

import pytest

TRACK_COUNT = 8  # master, inserts 1-6, FL's "current" track


@pytest.fixture
def eq(fl_modules):
    """Three bands per track: gain -18..+18 dB and 20 Hz..20 kHz (log), both 0-1 positions."""
    mixer = fl_modules["mixer"]
    state = {(t, b): {"gain": 0.5, "frequency": 0.5, "bandwidth": 0.27}
             for t in range(TRACK_COUNT) for b in range(3)}
    mixer.trackCount.return_value = TRACK_COUNT
    mixer.getEqBandCount.return_value = 3

    def gain(t, b, mode=0):
        value = state[(t, b)]["gain"]
        return (value - 0.5) * 36 if mode else value

    def frequency(t, b, mode=0):
        value = state[(t, b)]["frequency"]
        return 20 * 1000 ** value if mode else value

    mixer.getEqGain.side_effect = gain
    mixer.getEqFrequency.side_effect = frequency
    mixer.getEqBandwidth.side_effect = lambda t, b: state[(t, b)]["bandwidth"]
    mixer.setEqGain.side_effect = lambda t, b, v: state[(t, b)].update(gain=v)
    mixer.setEqFrequency.side_effect = lambda t, b, v: state[(t, b)].update(frequency=v)
    mixer.setEqBandwidth.side_effect = lambda t, b, v: state[(t, b)].update(bandwidth=v)
    return state


def test_get_eq_reads_every_band_in_db_and_hz(controller, eq):
    result = controller.dispatch_command("mixer.getEq", {"track": 2})

    assert result["track"] == 2
    assert [b["band"] for b in result["bands"]] == [0, 1, 2]
    assert result["bands"][0]["gain_db"] == 0
    assert result["bands"][0]["frequency_hz"] == pytest.approx(632.5, abs=1)


def test_set_eq_finds_the_positions_for_db_and_hz(controller, eq):
    result = controller.dispatch_command(
        "mixer.setEq", {"track": 2, "band": 1, "gain_db": -6, "frequency_hz": 300,
                        "bandwidth": 0.5})

    band = result["bands"][1]
    assert band["gain_db"] == pytest.approx(-6, abs=0.05)
    assert band["frequency_hz"] == pytest.approx(300, rel=0.005)
    assert band["bandwidth"] == 0.5
    assert eq[(2, 0)]["gain"] == 0.5  # other bands untouched


@pytest.mark.parametrize("params", [
    {"track": 2, "band": 3, "gain_db": 1},
    {"track": 9, "band": 0, "gain_db": 1},
    {"track": 2, "band": 0},
    {"track": 2, "band": 0, "gain_db": 40},
    {"track": 2, "band": 0, "frequency_hz": 5},
    {"track": 2, "band": 0, "bandwidth": 2},
    {"track": 2, "band": 0, "gain_db": "loud"},
])
def test_set_eq_refuses_bad_input_and_leaves_the_band(controller, eq, params):
    assert "error" in controller.dispatch_command("mixer.setEq", params)
    assert eq[(2, 0)] == {"gain": 0.5, "frequency": 0.5, "bandwidth": 0.27}


def test_time_signature_is_set_and_checked(controller, fl_modules):
    general = fl_modules["general"]
    state = {"num": 4, "den": 4}
    general.getRecPPQ.return_value = 96
    general.setNumerator.side_effect = lambda n: state.update(num=n)
    general.setDenominator.side_effect = lambda d: state.update(den=d)
    general.getRecPPB.side_effect = lambda: 96 * 4 * state["num"] // state["den"]

    result = controller.dispatch_command("project.setTimeSignature",
                                         {"numerator": 7, "denominator": 8})

    assert result == {"numerator": 7, "denominator": 8, "beats_per_bar": 3.5}


@pytest.mark.parametrize("params", [{"numerator": 0, "denominator": 4},
                                    {"numerator": 4, "denominator": 3},
                                    {"numerator": 4}])
def test_time_signature_refuses_bad_values(controller, fl_modules, params):
    assert "error" in controller.dispatch_command("project.setTimeSignature", params)
    fl_modules["general"].setNumerator.assert_not_called()


def test_redo_steps_forward(controller, fl_modules):
    general = fl_modules["general"]
    general.getUndoLevelHint.return_value = "3/9"

    result = controller.dispatch_command("project.redo", {"steps": 2})

    assert general.undoDown.call_count == 2
    assert result["redone"] == 2
    assert result["position"] == "3/9"


def test_undo_history_reports_position_and_unsaved_changes(controller, fl_modules):
    general = fl_modules["general"]
    general.getUndoLevelHint.return_value = "1/12"
    general.getUndoHistoryCount.return_value = 12
    general.getChangedFlag.return_value = 1

    assert controller.dispatch_command("project.getUndoHistory", {}) == {
        "position": "1/12", "count": 12, "unsaved_changes": True}


@pytest.fixture
def selection(fl_modules):
    arrangement = fl_modules["arrangement"]
    fl_modules["general"].getRecPPB.return_value = 384
    state = {"start": -1, "end": -1}
    arrangement.selectionIsActive.side_effect = lambda: int(state["start"] >= 0)
    arrangement.selectionStart.side_effect = lambda: state["start"]
    arrangement.selectionEnd.side_effect = lambda: state["end"]
    arrangement.selectionSet.side_effect = lambda s, e: state.update(start=s, end=e)
    arrangement.selectionClear.side_effect = lambda: state.update(start=-1, end=-1)
    return state


def test_selection_is_set_in_bars_and_cleared(controller, selection):
    result = controller.dispatch_command("arrangement.setSelection",
                                         {"start_bar": 9, "end_bar": 17})

    assert result == {"active": True, "start_bar": 9, "end_bar": 17}
    assert (selection["start"], selection["end"]) == (3072, 6144)
    assert controller.dispatch_command("arrangement.clearSelection", {}) == {"active": False}


@pytest.mark.parametrize("params", [{"start_bar": 0, "end_bar": 4}, {"start_bar": 5, "end_bar": 5},
                                    {"start_bar": 2}, {"start_bar": True, "end_bar": 3}])
def test_selection_refuses_bad_bars(controller, selection, params):
    assert "error" in controller.dispatch_command("arrangement.setSelection", params)
    assert selection["start"] == -1
