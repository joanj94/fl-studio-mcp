"""Setting plugin parameters by their display text (utils/param_values.py)."""

from __future__ import annotations

import math

import pytest

from fl_studio_mcp.utils.param_values import find_value, parse_display


class FakeParam:
    """A parameter: `show(v)` gives its text for position v; records every position tried."""

    def __init__(self, show):
        self.show = show
        self.tried: list[float] = []

    def read_at(self, value: float) -> str:
        self.tried.append(value)
        return self.show(value)


def eq_freq(v: float) -> str:  # Parametric EQ 2: 20 Hz to 20 kHz, logarithmic
    return f"{20 * 1000 ** v:.0f}Hz"


def reverb_high_cut(v: float) -> str:  # Reeverb 2: 0.5 kHz to 21.8 kHz, then "Off"
    return "Off" if v > 0.99 else f"{0.5 + 21.3 * v:.1f}kHz"


def band_type(v: float) -> str:
    names = ["Disabled", "Low pass", "Band pass", "High pass", "Notch", "Low shelf",
             "Peaking", "High shelf"]
    return names[round(v * 7)]


def limiter_ratio(v: float) -> str:  # 1:4.0 (expander) through 1:1.0 to 20.0:1
    r = 4 ** ((v - 0.5) * 2) if v < 0.5 else 1 + 19 * ((v - 0.5) * 2) ** 2
    return f"1:{1 / r:.1f}" if r < 1 else f"{r:.1f}:1"


@pytest.mark.parametrize("text, expected", [
    ("632Hz", ("frequency", 632.0)), ("4.0kHz", ("frequency", 4000.0)),
    ("0.0 dB", ("level", 0.0)), ("-INFdB", ("level", -math.inf)),
    ("85.53ms", ("time", 0.08553)), ("1.5sec", ("time", 1.5)), ("61%", ("percent", 61.0)),
    ("15.2 : 1", ("ratio", 15.2)), ("1:4.0", ("ratio", 0.25)), ("51", ("number", 51.0)),
    ("Peaking", None), ("", None),
])
def test_parse_display(text, expected):
    result = parse_display(text)
    if expected is None:
        assert result is None
    else:
        assert result[0] == expected[0] and result[1] == pytest.approx(expected[1])


def test_parse_display_reads_whole_number_colons_as_positions():
    assert parse_display("4:1", colon="position") == ("position", 4.001)


def test_bisection_finds_a_frequency():
    param = FakeParam(eq_freq)

    value, shows = find_value("250Hz", param.read_at)

    assert shows == "250Hz"
    assert 20 * 1000 ** value == pytest.approx(250, rel=0.01)
    assert len(param.tried) < 30


def test_kilohertz_target_on_a_hertz_display():
    _, shows = find_value("2.5kHz", FakeParam(eq_freq).read_at)
    assert abs(int(shows[:-2]) - 2500) <= 10


def test_a_named_end_is_stepped_over():
    value, shows = find_value("6kHz", FakeParam(reverb_high_cut).read_at)

    assert shows == "6.0kHz"
    assert value < 0.99


def test_named_setting_is_scanned():
    value, shows = find_value("high pass", FakeParam(band_type).read_at)

    assert shows == "High pass"
    assert round(value * 7) == 3


def test_ratio_target():
    _, shows = find_value("4:1", FakeParam(limiter_ratio).read_at)
    assert shows in ("3.9:1", "4.0:1", "4.1:1")


def test_out_of_range_is_refused():
    with pytest.raises(ValueError, match="outside"):
        find_value("30kHz", FakeParam(eq_freq).read_at)


def test_wrong_unit_is_refused():
    with pytest.raises(ValueError, match="same unit"):
        find_value("-6dB", FakeParam(eq_freq).read_at)


def test_unknown_setting_lists_the_settings():
    with pytest.raises(ValueError, match="Peaking"):
        find_value("Wobble", FakeParam(band_type).read_at)
