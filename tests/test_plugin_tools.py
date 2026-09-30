"""Plugin tools: a channel's plugin can be addressed by role, a mixer effect by track number."""

from __future__ import annotations

from typing import Any

import pytest

from fl_studio_mcp.tools import register_plugin_tools
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection({"channels.getAll": {"channels": [
        {"index": 0, "name": "808 Kick"}, {"index": 5, "name": "Lead"},
    ]}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def tools(fl) -> dict[str, Any]:
    collector = ToolCollector()
    register_plugin_tools(collector)
    return collector.tools


def _plugin_commands(fl: ScriptedConnection) -> list[tuple[str, dict]]:
    return [sent for sent in fl.sent if sent[0].startswith("plugins.")]


def test_a_channel_plugin_can_be_addressed_by_role(tools, fl):
    fl.results["plugins.setParamValue"] = {"name": "Cutoff", "value": 0.5, "value_string": "50%"}

    message = tools["fl_set_plugin_param_value"](param_index=3, value=0.5, plugin_index="lead")

    assert message == "Parameter 'Cutoff' set to 0.5000 (50%)"
    assert _plugin_commands(fl) == [("plugins.setParamValue", {
        "param_index": 3, "value": 0.5, "plugin_index": 5, "slot_index": -1, "use_global": True,
    })]


def test_every_plugin_tool_resolves_roles(tools, fl):
    for name in ("fl_is_plugin_valid", "fl_get_plugin_name", "fl_get_plugin_param_count",
                 "fl_get_plugin_params", "fl_get_preset_count", "fl_next_preset",
                 "fl_prev_preset", "fl_get_plugin_color"):
        tools[name]("lead")
    tools["fl_get_plugin_param_value"](0, "lead")

    sent = _plugin_commands(fl)
    assert len(sent) == 9
    assert all(params.get("index", params.get("plugin_index")) == 5 for _, params in sent)


def test_an_index_needs_no_role_lookup(tools, fl):
    tools["fl_get_plugin_name"](2)

    assert fl.sent == [("plugins.getName", {"index": 2, "slot_index": -1, "use_global": True})]


def test_mixer_effects_are_addressed_by_track_number(tools, fl):
    tools["fl_get_plugin_name"](3, slot_index=0)
    tools["fl_get_plugin_name"]("4", slot_index=1)

    assert [params["index"] for _, params in fl.sent] == [3, 4]
    assert "mixer track" in tools["fl_get_plugin_name"]("lead", slot_index=0)
    assert len(fl.sent) == 2


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("fl_is_plugin_valid", ("tuba",), False),
    ("fl_get_plugin_param_count", ("tuba",), -1),
    ("fl_get_preset_count", ("tuba",), -1),
])
def test_unknown_roles_give_each_tools_failure_value(tools, fl, name, args, expected):
    assert tools[name](*args) == expected
    assert _plugin_commands(fl) == []


def test_unknown_roles_are_reported_where_the_tool_can(tools, fl):
    assert "tuba" in tools["fl_next_preset"]("tuba")
    assert "tuba" in tools["fl_get_plugin_params"]("tuba")[0]["error"]
    assert "tuba" in tools["fl_get_plugin_param_value"](0, "tuba")["error"]
    assert _plugin_commands(fl) == []
