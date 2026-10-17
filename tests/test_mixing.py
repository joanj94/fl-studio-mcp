"""Mixing intents and chains (mixing/intents.py, tools/mixing.py)."""

from __future__ import annotations

import copy

import pytest

from fl_studio_mcp.mixing import intents as mixing
from fl_studio_mcp.tools import mixing as mixing_tools
from fl_studio_mcp.tools.mixing import pick_band, register_mixing_tools
from fl_studio_mcp.utils.param_values import parse_display
from fl_studio_mcp.utils.plugin_database import effect_browser_path
from tests.fakes import ToolCollector

DATA = mixing.load()


def _tools():
    collector = ToolCollector()
    register_mixing_tools(collector)
    return collector.tools


def test_shipped_data_is_consistent():
    mixing.validate(DATA)  # load() did it too; kept explicit
    assert {"vocal", "drum_bus", "master"} <= set(DATA["chains"])


@pytest.mark.parametrize("change, message", [
    (lambda d: d["intents"]["remove_mud"].pop("bands"), "go together"),
    (lambda d: d["intents"]["glue"]["set"].update({"Gain": {"from": "0dB", "to": "5Hz"}}),
     "same unit"),
    (lambda d: d["chains"]["vocal"]["steps"].append(["nothing"]), "unknown"),
    (lambda d: d["chains"]["vocal"]["steps"].append(["glue", "remove_mud"]), "one plugin"),
])
def test_validate_catches_bad_data(change, message):
    data = copy.deepcopy(DATA)
    change(data)
    with pytest.raises(ValueError, match=message):
        mixing.validate(data)


def test_settings_interpolate_by_amount_and_fill_the_band():
    values = mixing.settings(DATA["intents"]["remove_mud"], 0.5, band=3)

    assert values["Band 3 type"] == "Peaking"
    assert values["Band 3 freq"] == "250Hz"
    assert parse_display(values["Band 3 level"]) == ("level", -4.0)


def test_settings_check_amount_and_band():
    with pytest.raises(ValueError):
        mixing.settings(DATA["intents"]["glue"], 1.5)
    with pytest.raises(ValueError, match="band"):
        mixing.settings(DATA["intents"]["remove_mud"], 0.5)


def _band(type_: str = "Peaking", level: str = "0.0dB", freq: str = "632Hz") -> dict:
    return {"type": type_, "level": level, "freq": freq}


def test_pick_band_reuses_the_band_the_intent_set_before():
    bands = {b: _band() for b in range(1, 8)}
    bands[4] = _band(level="-6.0dB", freq="251Hz")

    assert pick_band(DATA["intents"]["remove_mud"], bands, set()) == 4


def test_pick_band_takes_the_first_flat_band_in_order():
    bands = {b: _band() for b in range(1, 8)}
    bands[3] = _band(level="-3.0dB", freq="1000Hz")  # in use for something else

    assert pick_band(DATA["intents"]["remove_mud"], bands, set()) == 2
    assert pick_band(DATA["intents"]["remove_mud"], bands, {2}) == 4


def test_pick_band_never_takes_a_filter_band():
    bands = {b: _band() for b in range(1, 8)}
    bands[1] = _band(type_="High pass", freq="80Hz")
    bands[2] = _band(type_="Low pass")

    with pytest.raises(ValueError, match="No free EQ band"):
        pick_band(DATA["intents"]["add_body"], {2: bands[2], 3: _band(level="2dB")}, set())
    assert pick_band(DATA["intents"]["high_pass"], bands, set()) == 1  # its own band


@pytest.fixture
def fake_mixer(monkeypatch):
    """Track effects, a load that adds a plugin, and parameters set by value."""
    state = {"effects": [], "set": [], "loads": []}
    monkeypatch.setattr(mixing_tools, "_effects", lambda track: list(state["effects"]))

    def run_load(action, path, track):
        state["loads"].append(path)
        slot = len(state["effects"])
        state["effects"].append({"slot": slot, "plugin": path.rsplit("/", 1)[1]})
        return {"track": track, "slot": slot}

    monkeypatch.setattr(mixing_tools, "run_load", run_load)
    monkeypatch.setattr(mixing_tools, "effect_browser_path",
                        lambda name: f"Plugin database/Effects/X/{name}")
    monkeypatch.setattr(mixing_tools, "param_by_name", lambda conn, t, s, name: (0, name))
    monkeypatch.setattr(mixing_tools, "_bands",
                        lambda conn, t, s: {b: _band() for b in range(1, 8)})

    def set_value(conn, track, slot, index, value):
        state["set"].append((slot, value))
        return {"shows": value}

    monkeypatch.setattr(mixing_tools, "set_param_by_value", set_value)
    monkeypatch.setattr(mixing_tools.connection, "get_connection", lambda: None)
    return state


def test_apply_intent_adds_the_plugin_when_the_track_lacks_it(fake_mixer):
    result = _tools()["fl_apply_intent"](3, "glue", amount=1.0)

    assert result["added"] is True and result["plugin"] == "Fruity Limiter"
    assert fake_mixer["loads"] == ["Plugin database/Effects/X/Fruity Limiter"]
    assert result["applied"][0]["set"]["Comp threshold"] == "-12.0dB"


def test_apply_intent_reuses_a_plugin_on_the_track(fake_mixer):
    fake_mixer["effects"].append({"slot": 4, "plugin": "Fruity limiter"})

    result = _tools()["fl_apply_intent"](3, "glue")

    assert result["slot"] == 4 and result["added"] is False and not fake_mixer["loads"]


def test_two_eq_intents_in_one_call_take_different_bands(fake_mixer):
    result = _tools()["fl_apply_intent"](3, ["remove_mud", "remove_boxiness"])

    assert [a["band"] for a in result["applied"]] == [3, 4]


def test_mixed_plugins_and_bad_amount_are_refused_before_anything_changes(fake_mixer):
    tools = _tools()
    assert "error" in tools["fl_apply_intent"](3, ["glue", "remove_mud"])
    assert "error" in tools["fl_apply_intent"](3, "glue", amount=2)
    assert "error" in tools["fl_apply_intent"](3, "nothing")
    assert not fake_mixer["loads"] and not fake_mixer["set"]


def test_setup_chain_builds_every_step(fake_mixer):
    result = _tools()["fl_setup_chain"](5, "vocal")

    plugins = [step["plugin"] for step in result["steps"]]
    assert plugins == ["Fruity Parametric EQ 2", "Fruity Limiter", "Fruity Reeverb 2"]
    assert "error" in _tools()["fl_setup_chain"](5, "nothing")


def test_effect_browser_path_finds_the_database_entry(settings_dir):
    effects = settings_dir.parent / "Presets" / "Plugin database" / "Effects" / "Filter"
    effects.mkdir(parents=True)
    (effects / "Fruity Parametric EQ 2.fst").write_bytes(b"")

    assert effect_browser_path("fruity parametric eq 2") == (
        "Plugin database/Effects/Filter/Fruity Parametric EQ 2"
    )
    with pytest.raises(ValueError):
        effect_browser_path("Nothing")
