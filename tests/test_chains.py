"""Effect chains kept under a name and put on another mixer track."""

from __future__ import annotations

import pytest

from fl_studio_mcp.tools import chains
from tests.fakes import ToolCollector

TRACK_2 = {"index": 2, "name": "Kick", "effects": [
    {"slot": 0, "plugin": "Fruity parametric EQ 2", "enabled": True, "mix": 1.0,
     "params": [0.1, 0.2], "params_truncated": False},
    {"slot": 3, "plugin": "Fruity Limiter", "enabled": False, "mix": 0.5,
     "params": [0.7], "params_truncated": False}]}


@pytest.fixture
def fl(settings_dir, monkeypatch):
    sent, loads = [], []
    free = iter(range(5, 10))

    def long_call(action, params=None):
        sent.append((action, params))
        if action == "project.getSnapshot":
            return {"tracks": [TRACK_2, {"index": 4, "name": "Bass", "effects": []}]}
        return {"changed": 3, "skipped": []}

    def load(plugin, track):
        loads.append((plugin, track))
        if plugin == "Missing":
            return {"error": "not in the plugin database"}
        return {"slot": next(free), "plugin": plugin.upper() if "EQ" in plugin else plugin}

    monkeypatch.setattr(chains, "_long_call", long_call)
    monkeypatch.setattr(chains, "_load", load)
    tools = ToolCollector()
    chains.register_chain_tools(tools)
    return tools.tools, sent, loads


def test_a_chain_is_kept_and_put_on_another_track_in_order(fl):
    tools, sent, loads = fl

    saved = tools["fl_save_chain"](2, "kick bus")
    applied = tools["fl_apply_chain"](4, "kick bus")

    assert saved["effects"] == ["Fruity parametric EQ 2", "Fruity Limiter"]
    assert loads == [("Fruity parametric EQ 2", 4), ("Fruity Limiter", 4)]
    action, params = sent[-1]
    assert action == "project.applyState" and params["channels"] == []
    effects = params["tracks"][0]["effects"]
    assert params["tracks"][0]["index"] == 4
    assert [(e["slot"], e["plugin"], e["enabled"], e["params"]) for e in effects] == [
        (5, "FRUITY PARAMETRIC EQ 2", True, [0.1, 0.2]), (6, "Fruity Limiter", False, [0.7])]
    assert applied["loaded"] == [{"slot": 5, "plugin": "FRUITY PARAMETRIC EQ 2"},
                                 {"slot": 6, "plugin": "Fruity Limiter"}]
    assert tools["fl_list_chains"]()["chains"][0]["name"] == "kick bus"


def test_loading_stops_at_the_first_plugin_that_fails(fl, monkeypatch):
    tools, sent, loads = fl
    tools["fl_save_chain"](2, "c")
    path = chains._chain_path("c")
    path.write_text(path.read_text().replace("Fruity Limiter", "Missing"))

    result = tools["fl_apply_chain"](4, "c")

    assert "Missing" in result["error"] and len(result["loaded"]) == 1
    assert sent[-1][1]["tracks"][0]["effects"][0]["slot"] == 5  # the first is still set


@pytest.mark.parametrize("call", [
    lambda t: t["fl_save_chain"](4, "empty"),  # no effects
    lambda t: t["fl_save_chain"](9, "x"),  # track not in use
    lambda t: t["fl_save_chain"](2, "../up"),
    lambda t: t["fl_apply_chain"](2, "nothing"),
])
def test_errors(fl, call):
    assert "error" in call(fl[0])


def test_windows_device_names_are_refused(settings_dir):
    for name in ("NUL", "con", "COM1.backup"):
        with pytest.raises(ValueError, match="device"):
            chains._chain_path(name)
