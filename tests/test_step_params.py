"""Step parameter conversion between friendly units and FL's raw values."""

from __future__ import annotations

import pytest

from fl_studio_mcp.utils.step_params import step_from_fl, step_to_fl


def test_friendly_values_become_fl_units():
    step = {"step": 3, "on": True, "pitch": "C3", "velocity": 1.0, "release": 0.5,
            "pan": -1.0, "fine_pitch": -50, "mod_x": 0.0, "mod_y": 1.0, "shift": 6}

    # FL's step values run 0-128 (seen live: mod X/Y default to 128), like pan.
    assert step_to_fl(step) == {"step": 3, "on": True, "pitch": 48, "velocity": 128,
                                "release": 64, "pan": 0, "fine_pitch": 115, "mod_x": 0,
                                "mod_y": 128, "shift": 6}


def test_only_given_fields_are_sent():
    assert step_to_fl({"step": 0, "velocity": 0.5}) == {"step": 0, "velocity": 64}


def test_full_right_pan_and_extreme_fine_pitch():
    assert step_to_fl({"step": 0, "pan": 1.0, "fine_pitch": 1200}) == {
        "step": 0, "pan": 128, "fine_pitch": 240,
    }


@pytest.mark.parametrize("step", [
    {"velocity": 0.5},                    # no step index
    {"step": -1, "velocity": 0.5},
    {"step": 1.5},
    {"step": 0, "velocity": 1.2},
    {"step": 0, "pan": -1.5},
    {"step": 0, "fine_pitch": 1300},
    {"step": 0, "shift": -2},
    {"step": 0, "pitch": 200},
    {"step": 0, "velocity": "loud"},
    {"step": 0, "on": "yes"},
    {"step": 0, "wobble": 1},
])
def test_invalid_steps_are_rejected(step):
    with pytest.raises(ValueError):
        step_to_fl(step)


def test_fl_values_become_friendly_units():
    raw = {"step": 1, "on": True, "pitch": 48, "velocity": 128, "release": 64, "fine_pitch": 115,
           "pan": 64, "mod_x": 0, "mod_y": 128, "shift": 0}

    assert step_from_fl(raw) == {"step": 1, "on": True, "pitch": 48, "note": "C3",
                                 "velocity": 1.0, "release": 0.5, "fine_pitch": -50,
                                 "pan": 0.0, "mod_x": 0.0, "mod_y": 1.0, "shift": 0}


def test_inactive_step_passes_through():
    assert step_from_fl({"step": 2, "on": False}) == {"step": 2, "on": False}
