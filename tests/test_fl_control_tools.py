"""Server-side tools for tempo, project overview, patterns and step parameters."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import (
    register_channel_tools,
    register_pattern_tools,
    register_project_tools,
)
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection()
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def tools(fl) -> dict[str, Any]:
    collector = ToolCollector()
    for register in (register_project_tools, register_pattern_tools, register_channel_tools):
        register(collector)
    return collector.tools


def test_call_strips_protocol_fields(fl):
    fl.results["project.getTempo"] = {"bpm": 150.0}

    assert connection.call("project.getTempo") == {"bpm": 150.0}


def test_call_turns_failures_into_errors(fl):
    fl.results["project.getTempo"] = {"error": "boom"}

    assert connection.call("project.getTempo") == {"error": "boom"}


def test_set_tempo(tools, fl):
    fl.results["project.setTempo"] = {"bpm": 150.0}

    assert tools["fl_set_tempo"](150) == {"bpm": 150.0}
    assert fl.sent == [("project.setTempo", {"bpm": 150})]


@pytest.mark.parametrize("bpm", [0, 9.9, 523])
def test_set_tempo_validates_before_sending(tools, fl, bpm):
    assert "error" in tools["fl_set_tempo"](bpm)
    assert fl.sent == []


def test_get_tempo_and_overview_pass_results_through(tools, fl):
    fl.results["project.getInfo"] = {"bpm": 140.0, "patterns": []}

    assert tools["fl_get_project_overview"]() == {"bpm": 140.0, "patterns": []}
    assert "error" not in tools["fl_get_tempo"]()


def test_pattern_tools_send_expected_commands(tools, fl):
    tools["fl_get_patterns"]()
    tools["fl_select_pattern"](4)
    tools["fl_rename_pattern"]("Drop", index=4)
    tools["fl_rename_pattern"]("Current")
    tools["fl_find_empty_pattern"]()

    assert fl.sent == [
        ("patterns.getAll", {}),
        ("patterns.select", {"index": 4}),
        ("patterns.rename", {"index": 4, "name": "Drop"}),
        ("patterns.rename", {"name": "Current"}),
        ("patterns.findEmpty", {}),
    ]


def test_select_pattern_validates_index(tools, fl):
    assert "error" in tools["fl_select_pattern"](0)
    assert fl.sent == []


def test_set_step_params_converts_units(tools, fl):
    fl.results["channels.setStepParams"] = {"steps_written": 2, "pattern": 1}

    result = tools["fl_set_step_params"](
        channel=2, steps=[{"step": 0, "on": True, "pitch": "C3", "velocity": 1.0}], pattern=3
    )

    assert result == {"steps_written": 2, "pattern": 1}
    assert fl.sent == [("channels.setStepParams", {
        "channel": 2, "pattern": 3,
        "steps": [{"step": 0, "on": True, "pitch": 48, "velocity": 128}],
    })]


def test_set_step_params_reports_invalid_steps_without_sending(tools, fl):
    result = tools["fl_set_step_params"](channel=0, steps=[{"step": 0, "velocity": 3}])

    assert "velocity" in result["error"]
    assert fl.sent == []


def test_get_step_params_converts_back(tools, fl):
    fl.results["channels.getStepParams"] = {
        "pattern": 1,
        "steps": [{"step": 0, "on": False}, {"step": 1, "on": True, "velocity": 128, "pan": 64}],
    }

    result = tools["fl_get_step_params"](channel=0, steps=2)

    assert result == {"pattern": 1, "steps": [
        {"step": 0, "on": False},
        {"step": 1, "on": True, "velocity": 1.0, "pan": 0.0},
    ]}
