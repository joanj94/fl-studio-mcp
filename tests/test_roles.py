"""Roles: addressing channels by a word in their name instead of an index."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import register_channel_tools, register_roles_tools
from fl_studio_mcp.utils import connection
from fl_studio_mcp.utils.roles import check_roles, match_role, resolve_channel
from tests.fakes import ScriptedConnection, ToolCollector

CHANNELS = [
    {"index": 0, "name": "808 Kick", "target_fx_track": 1, "plugin": None},
    {"index": 1, "name": "Kick Top", "target_fx_track": 2, "plugin": "Sytrus"},
    {"index": 2, "name": "Kick", "target_fx_track": 1, "plugin": None},
    {"index": 3, "name": "Sub Bass", "target_fx_track": 3, "plugin": "3x Osc"},
    {"index": 4, "name": "Lead_Main", "target_fx_track": 4, "plugin": "Sytrus"},
    {"index": 5, "name": "Pad", "target_fx_track": 5, "plugin": None},
    {"index": 6, "name": "Pad", "target_fx_track": 6, "plugin": None},
    {"index": 7, "name": "Bassline", "target_fx_track": 7, "plugin": None},
]


@pytest.mark.parametrize("role, index", [
    ("kick", 2),  # an exact name beats word matches ("808 Kick", "Kick Top")
    ("KICK TOP", 1),
    ("sub bass", 3),
    ("  Sub-Bass ", 3),  # punctuation and spacing don't matter
    ("lead", 4),  # "_" separates words
    ("main", 4),
])
def test_match_role(role, index):
    assert match_role(role, CHANNELS)["index"] == index


def test_role_must_match_whole_words():
    with pytest.raises(ValueError, match="No channel") as error:
        match_role("bas", CHANNELS)
    assert "Sub Bass" in str(error.value)  # lists what exists


def test_word_match_does_not_reach_inside_words():
    assert match_role("bass", CHANNELS)["index"] == 3  # not "Bassline"


@pytest.mark.parametrize("role", ["pad", "808"])
def test_ambiguous_role_is_an_error(role):
    channels = [*CHANNELS, {"index": 8, "name": "808 Snare"}]
    with pytest.raises(ValueError, match="matches several"):
        match_role(role, channels)


@pytest.mark.parametrize("role", ["", "  ", "--"])
def test_empty_role_is_an_error(role):
    with pytest.raises(ValueError):
        match_role(role, CHANNELS)


def test_check_roles_sorts_found_missing_and_ambiguous():
    assert check_roles(["kick", "lead", "vocal", "pad"], CHANNELS) == {
        "ok": False,
        "found": {
            "kick": {"channel": 2, "name": "Kick"},
            "lead": {"channel": 4, "name": "Lead_Main"},
        },
        "missing": ["vocal"],
        "ambiguous": {"pad": ["Pad", "Pad"]},
    }


def test_check_roles_ok_when_all_found():
    assert check_roles(["kick", "sub bass"], CHANNELS)["ok"] is True


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection({"channels.getAll": {"channels": CHANNELS}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


@pytest.mark.parametrize("channel", [4, "4", " 4 "])
def test_resolve_channel_passes_indexes_through(fl, channel):
    assert resolve_channel(channel) == 4
    assert fl.sent == []


def test_resolve_channel_looks_up_roles(fl):
    assert resolve_channel("sub bass") == 3
    assert fl.sent == [("channels.getAll", {})]


@pytest.mark.parametrize("channel", [True, -1, 2.5, None])
def test_resolve_channel_rejects_other_values(fl, channel):
    with pytest.raises(ValueError):
        resolve_channel(channel)


def test_resolve_channel_reports_fl_errors(fl):
    fl.results["channels.getAll"] = {"error": "not connected"}
    with pytest.raises(ValueError, match="not connected"):
        resolve_channel("kick")


@pytest.fixture
def tools(fl) -> dict[str, Any]:
    collector = ToolCollector()
    register_roles_tools(collector)
    register_channel_tools(collector)
    return collector.tools


def test_get_roles_lists_channels_with_mixer_track_and_plugin(tools):
    roles = tools["fl_get_roles"]()["roles"]

    assert roles[3] == {"channel": 3, "name": "Sub Bass", "mixer_track": 3, "plugin": "3x Osc"}
    assert len(roles) == len(CHANNELS)


def test_check_roles_tool(tools):
    assert tools["fl_check_roles"](["kick", "vocal"])["missing"] == ["vocal"]


def test_role_tools_pass_fl_errors_through(tools, fl):
    fl.results["channels.getAll"] = {"error": "not connected"}

    assert tools["fl_get_roles"]() == {"error": "not connected"}
    assert tools["fl_check_roles"](["kick"]) == {"error": "not connected"}


def test_step_tools_accept_a_role(tools, fl):
    tools["fl_set_step_params"]("sub bass", [{"step": 0, "on": True}])
    tools["fl_get_step_params"]("lead")
    tools["fl_set_step_sequence"]("kick", [True, False])
    tools["fl_get_step_sequence"]("kick")

    sent = [(action, params.get("channel")) for action, params in fl.sent if params]
    assert sent == [
        ("channels.setStepParams", 3),
        ("channels.getStepParams", 4),
        ("channels.setStepSequence", 2),
        ("channels.getStepSequence", 2),
    ]


def test_step_tools_report_unknown_roles_without_writing(tools, fl):
    assert "No channel" in tools["fl_set_step_params"]("vocal", [{"step": 0}])["error"]
    assert "No channel" in tools["fl_get_step_params"]("vocal")["error"]
    assert "No channel" in tools["fl_set_step_sequence"]("vocal", [True])
    assert "No channel" in tools["fl_get_step_sequence"]("vocal")["error"]
    assert [action for action, _ in fl.sent] == ["channels.getAll"] * 4


def test_negative_number_string_is_treated_as_a_role(fl):
    with pytest.raises(ValueError, match="No channel matches role '-1'"):
        resolve_channel("-1")
    assert fl.sent == [("channels.getAll", {})]


def test_get_step_sequence_reports_fl_errors(tools, fl):
    fl.results["channels.getStepSequence"] = {"error": "not connected"}

    assert tools["fl_get_step_sequence"](2) == {"error": "not connected"}
