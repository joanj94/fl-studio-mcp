"""FL_MCP_PROFILE chooses which groups of tools the server offers."""

from __future__ import annotations

import pytest

from fl_studio_mcp import profiles
from tests.fakes import ToolCollector


def _tools(value: str) -> set[str]:
    collector = ToolCollector()
    profiles.register_profile(collector, value)
    return set(collector.tools)


def test_all_is_the_default_and_registers_every_group():
    assert profiles.groups_for(None) == list(profiles.GROUPS)
    assert profiles.groups_for("") == list(profiles.GROUPS)


def test_every_profile_names_known_groups():
    for groups in profiles.PROFILES.values():
        assert set(groups) <= set(profiles.GROUPS)


def test_a_smaller_profile_offers_fewer_tools():
    everything = _tools("all")
    minimal = _tools("minimal")

    assert "fl_play" in minimal or "fl_set_tempo" in minimal
    assert "fl_render_stems" not in minimal and "music_chords" not in minimal
    assert minimal < everything


def test_profiles_combine():
    combined = _tools("compose, MIX")

    assert "music_chords" in combined and "fl_render_stems" in combined
    assert combined == _tools("compose") | _tools("mix")


def _batch(profile="all"):
    collector = ToolCollector()
    profiles.register_profile(collector, profile)
    return collector.tools["fl_batch"]


def test_batch_runs_tools_in_order_and_stops_at_an_error():
    batch = _batch("analysis")

    result = batch([{"tool": "music_reference"},
                    {"tool": "music_chords", "args": {"chords": ["Hm7b"]}},
                    {"tool": "music_reference"}])

    assert [r["tool"] for r in result["results"]] == ["music_reference", "music_chords"]
    assert "error" in result["results"][1]["result"] and result["completed"] is False


def test_batch_can_go_on_after_an_error_and_reports_bad_arguments():
    result = _batch("analysis")([{"tool": "music_reference", "args": {"nope": 1}},
                                 {"tool": "music_reference"}], stop_on_error=False)

    assert len(result["results"]) == 2 and "nope" in result["results"][0]["result"]["error"]


def test_batch_refuses_unknown_tools_before_running_anything():
    assert "fl_play" in _batch("analysis")([{"tool": "fl_play"}])["error"]
    assert "error" in _batch()([])
    assert "error" in _batch()([{"tool": "fl_batch"}])


def test_unknown_profile_is_refused(monkeypatch):
    monkeypatch.setenv(profiles.ENV_VAR, "mastering")

    with pytest.raises(ValueError, match="mastering"):
        profiles.register_profile(ToolCollector())


def test_a_sentence_reporting_an_error_counts_as_a_failure():
    calls = []
    functions = {"say": lambda text: calls.append(text) or text}

    result = profiles.run_batch(functions, [{"tool": "say", "args": {"text": "Error: no"}},
                                            {"tool": "say", "args": {"text": "fine"}}])

    assert calls == ["Error: no"] and result["completed"] is False


def test_a_tool_that_raises_keeps_the_earlier_results():
    def boom():
        raise KeyError("x")

    functions = {"ok": lambda: {"done": True}, "boom": boom}
    result = profiles.run_batch(functions, [{"tool": "ok"}, {"tool": "boom"}, {"tool": "ok"}])

    assert result["results"][0]["result"] == {"done": True}
    assert "KeyError" in result["results"][1]["result"]["error"] and len(result["results"]) == 2


def test_a_type_error_inside_a_tool_isnt_called_a_wrong_argument():
    functions = {"bug": lambda: 1 + "a"}

    error = profiles.run_batch(functions, [{"tool": "bug"}])["results"][0]["result"]["error"]

    assert "TypeError" in error and "Wrong arguments" not in error
    assert "Wrong arguments" in profiles.run_batch(
        functions, [{"tool": "bug", "args": {"x": 1}}])["results"][0]["result"]["error"]


def test_a_tool_name_that_isnt_text_is_refused():
    assert "error" in profiles.run_batch({"ok": lambda: 1}, [{"tool": ["ok"]}])
