"""Bringing a song to a loudness with the master's limiter, render by render."""

from __future__ import annotations

import pytest

from fl_studio_mcp.audio.loudness_target import MAX_STEP_DB, next_gain
from fl_studio_mcp.tools import mix_check
from tests.fakes import ToolCollector


def test_first_step_is_one_for_one():
    assert next_gain([(0.0, -20.0)], -14.0) == 6.0


def test_later_steps_follow_the_limiters_slope():
    # 6 dB gave 3 LU: half a LU per dB, so 2 LU more needs 4 dB.
    assert next_gain([(0.0, -20.0), (6.0, -17.0)], -15.0) == pytest.approx(10.0)


def test_renders_on_both_sides_are_interpolated():
    # Seen live: 0.7 dB gave -18.9 LUFS, 7.5 dB gave -12.4; -14 lies between.
    history = [(12.3, -11.9), (10.2, -12.1), (0.7, -18.9), (7.5, -12.4)]

    assert next_gain(history, -14.0) == pytest.approx(5.83, abs=0.01)


def test_steps_stay_bounded():
    assert next_gain([(0.0, -40.0)], -6.0) == MAX_STEP_DB
    assert next_gain([(0.0, -20.0), (6.0, -19.99)], -10.0) <= 6.0 + MAX_STEP_DB
    with pytest.raises(ValueError):
        next_gain([], -14.0)


class Limiter:
    """A master whose loudness follows the gain with a limiter's knee above -12 LUFS."""

    def __init__(self):
        self.gain = 0.0
        self.set_values: list[str] = []

    def loudness(self) -> float:
        raw = -20.0 + self.gain
        return round(raw if raw < -12 else -12 + (raw + 12) * 0.5, 1)


@pytest.fixture
def master(monkeypatch):
    limiter = Limiter()

    class Conn:
        def send_command(self, action, params=None, timeout=2.0):
            return {"success": True, "value_string": f"{limiter.gain:+.1f}dB"}

    def set_by_value(conn, track, slot, index, value):
        limiter.set_values.append(value)
        if index == "gain":
            limiter.gain = float(value.removesuffix("dB"))
            return {"shows": f"{limiter.gain:+.1f}dB"}
        return {"shows": value}

    monkeypatch.setattr(mix_check, "get_connection", lambda: Conn())
    monkeypatch.setattr(mix_check, "find_or_add_plugin", lambda track, plugin: (3, True))
    monkeypatch.setattr(mix_check, "param_by_name",
                        lambda conn, track, slot, name: (name.split()[-1].lower(), name))
    monkeypatch.setattr(mix_check, "set_param_by_value", set_by_value)
    monkeypatch.setattr(mix_check, "_render_mix",
                        lambda timeout: {"loudness_lufs": limiter.loudness(), "peak_dbfs": -1.0})
    tools = ToolCollector()
    mix_check.register_mix_check_tools(tools)
    return limiter, tools.tools["fl_master_to_loudness"]


def test_reaches_the_target_through_the_knee(master):
    limiter, tool = master

    result = tool(target_lufs=-9.0, ceiling_db=-0.5)

    assert result["reached"] is True
    assert abs(result["renders"][-1]["loudness_lufs"] + 9.0) <= 0.5
    assert len(result["renders"]) <= 5
    assert limiter.set_values[0] == "-0.5dB"  # the ceiling first
    assert result["slot"] == 3 and result["added"] is True


def test_stops_after_max_renders(master):
    _, tool = master

    result = tool(target_lufs=-3.0, max_renders=2)

    assert len(result["renders"]) == 2 and result["reached"] is False


@pytest.mark.parametrize("kwargs", [{"target_lufs": 3}, {"ceiling_db": -20},
                                    {"max_renders": 0}, {"tolerance": 0}])
def test_bad_arguments_are_refused(master, kwargs):
    assert "error" in master[1](**kwargs)


def test_stops_at_the_limiters_top_gain(master):
    limiter, tool = master

    result = tool(target_lufs=-1.0, max_renders=8)

    assert limiter.gain == mix_check.MAX_LIMITER_GAIN_DB
    assert result["reached"] is False and "top" in result["note"]
    assert len(result["renders"]) < 8


def test_a_failed_analysis_is_reported_with_the_limiters_state(master, monkeypatch):
    monkeypatch.setattr(mix_check, "_render_mix", lambda timeout: {"error": "bad wav"})

    result = master[1]()

    assert "bad wav" in result["error"] and "slot 3" in result["error"]
    assert "added by this run" in result["error"]
