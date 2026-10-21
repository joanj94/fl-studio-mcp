"""Ducking a track under the kick with volume automation (tools/ducking.py)."""

from __future__ import annotations

import pytest

from fl_studio_mcp.tools import ducking
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector


def test_each_hit_drops_at_once_and_comes_back():
    points = ducking.duck_points([0, 1, 2, 3], 4, 5, 1, -3.0, 6.0, 0.5, 4)

    assert points[:4] == [{"bar": 5.0, "db": -3.0}, {"bar": 5.0, "db": -3.0},
                          {"bar": 5.0, "db": -9.0},
                          {"bar": 5.125, "db": -3.0, "tension": 0.0}]
    assert sum(p["db"] == -9.0 for p in points) == 4
    assert points[-1] == {"bar": 6.0, "db": -3.0}  # held to the end of the stretch
    assert [p["bar"] for p in points] == sorted(p["bar"] for p in points)


def test_a_release_longer_than_the_gap_is_cut_at_the_next_hit():
    points = ducking.duck_points([0, 0.5], 4, 1, 1, 0.0, 10.0, 2.0, 4)

    back = [p for p in points if "tension" in p]
    assert back[0]["bar"] == pytest.approx(1 + 0.5 / 4)  # where the second hit comes
    assert back[1]["bar"] == pytest.approx(1 + 2.5 / 4)


def test_a_pattern_longer_than_a_bar_repeats_as_given():
    points = ducking.duck_points([0, 6], 8, 1, 4, 0.0, 6.0, 0.25, 4)
    assert sum(p["db"] == -6.0 for p in points) == 4  # 16 beats: 0, 6, 8, 14


@pytest.mark.parametrize("kwargs", [
    {"hits": []}, {"hits": [4]}, {"depth_db": 0}, {"depth_db": 50},
    {"release_beats": 0}, {"bars": 0}, {"start_bar": 0}, {"bars": 200},
])
def test_bad_ducking_is_refused(kwargs):
    given = {"hits": [0, 1, 2, 3], "repeat_beats": 4, "start_bar": 1, "bars": 8,
             "level_db": 0.0, "depth_db": 6.0, "release_beats": 0.5, "beats_per_bar": 4,
             **kwargs}
    with pytest.raises(ValueError):
        ducking.duck_points(**given)


@pytest.fixture
def duck(monkeypatch):
    conn = ScriptedConnection({"project.getInfo": {"beats_per_bar": 4},
                               "mixer.getTrackInfo": {"volume_db": -4.5}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    made = []
    monkeypatch.setattr(ducking, "automate", lambda points, **kw: made.append(
        (points, kw)) or {"channel": 6, "name": kw["name"]})
    collector = ToolCollector()
    ducking.register_ducking_tools(collector)
    return collector.tools["fl_duck_track"], made


def test_the_tool_ducks_from_the_faders_level(duck):
    tool, made = duck

    result = tool(3, start_bar=9, bars=2)

    points, kw = made[0]
    assert kw == {"mixer_track": 3, "parameter": "volume", "name": "Duck 3"}
    assert points[0] == {"bar": 9.0, "db": -4.5} and result["hits"] == 8
    assert min(p["db"] for p in points) == -10.5


def test_the_tool_reports_bad_arguments_without_automating(duck):
    tool, made = duck
    assert "error" in tool(3, hits=[5])
    assert made == []
