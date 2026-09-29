"""Step sequencer parameters: friendly units <-> FL Studio's raw values.

Friendly units match the rest of the server: velocity-like values 0.0-1.0,
pan -1.0 (left) to 1.0 (right), fine pitch in cents, pitch as a MIDI number or
note name (C4 = 60). FL stores velocity, release, mod X/Y and pan as 0-128
(pan 64 = centre; mod X/Y read 128 by default, seen live in FL 2026), fine
pitch as 0-240 (120 = no detune, 10 cents per unit) and shift in ticks.
"""

from __future__ import annotations

from typing import Any

from fl_studio_mcp.music.model import parse_pitch, pitch_name

FL_VALUE_MAX = 128
FRACTION_PARAMS = ("velocity", "release", "mod_x", "mod_y")
PAN_CENTER = 64
FINE_PITCH_CENTER = 120
CENTS_PER_FINE_UNIT = 10
MAX_CENTS = 1200
KNOWN_FIELDS = ("step", "on", "pitch", *FRACTION_PARAMS, "pan", "fine_pitch", "shift")
# Decimal places kept when converting FL's integers back to fractions.
FRACTION_DECIMALS = 3


def _number(step: dict[str, Any], key: str, low: float, high: float) -> float:
    value = step[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Step field '{key}' must be a number, got {value!r}")
    if not low <= value <= high:
        raise ValueError(f"Step field '{key}' must be {low} to {high}, got {value}")
    return value


def _integer(step: dict[str, Any], key: str) -> int:
    value = step.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Step field '{key}' must be a non-negative integer, got {value!r}")
    return value


def step_to_fl(step: dict[str, Any]) -> dict[str, Any]:
    """FL's raw values for the fields present in `step` (which needs a "step" index)."""
    unknown = sorted(set(step) - set(KNOWN_FIELDS))
    if unknown:
        raise ValueError(f"Unknown step field(s) {unknown}; use {list(KNOWN_FIELDS)}")

    raw: dict[str, Any] = {"step": _integer(step, "step")}
    if "on" in step:
        if not isinstance(step["on"], bool):
            raise ValueError(f"Step field 'on' must be true or false, got {step['on']!r}")
        raw["on"] = step["on"]
    if "pitch" in step:
        raw["pitch"] = parse_pitch(step["pitch"])
    for key in FRACTION_PARAMS:
        if key in step:
            raw[key] = round(_number(step, key, 0.0, 1.0) * FL_VALUE_MAX)
    if "pan" in step:
        raw["pan"] = round(PAN_CENTER + _number(step, "pan", -1.0, 1.0) * PAN_CENTER)
    if "fine_pitch" in step:
        cents = _number(step, "fine_pitch", -MAX_CENTS, MAX_CENTS)
        raw["fine_pitch"] = round(FINE_PITCH_CENTER + cents / CENTS_PER_FINE_UNIT)
    if "shift" in step:
        raw["shift"] = _integer(step, "shift")
    return raw


def step_from_fl(raw: dict[str, Any]) -> dict[str, Any]:
    """Friendly units for a step as the controller reports it."""
    step = {"step": raw["step"], "on": raw["on"]}
    if "pitch" in raw:
        step["pitch"] = raw["pitch"]
        step["note"] = pitch_name(raw["pitch"])
    for key in FRACTION_PARAMS:
        if key in raw:
            step[key] = round(raw[key] / FL_VALUE_MAX, FRACTION_DECIMALS)
    if "fine_pitch" in raw:
        step["fine_pitch"] = (raw["fine_pitch"] - FINE_PITCH_CENTER) * CENTS_PER_FINE_UNIT
    if "pan" in raw:
        step["pan"] = round((raw["pan"] - PAN_CENTER) / PAN_CENTER, FRACTION_DECIMALS)
    if "shift" in raw:
        step["shift"] = raw["shift"]
    return step
