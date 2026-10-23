"""FL-side controller: raw API calls and listings (api.call, api.list)."""

from __future__ import annotations

import types

import pytest


def test_call_runs_one_api_function_with_its_arguments(controller, fl_modules):
    fl_modules["mixer"].getTrackName.return_value = "Kick"

    result = controller.dispatch_command(
        "api.call", {"module": "mixer", "function": "getTrackName", "args": [3]}
    )

    assert result == {"module": "mixer", "function": "getTrackName", "result": "Kick"}
    fl_modules["mixer"].getTrackName.assert_called_once_with(3)


def test_call_passes_keyword_arguments(controller, fl_modules):
    controller.dispatch_command("api.call", {
        "module": "channels", "function": "getChannelVolume", "args": [2],
        "kwargs": {"mode": True},
    })
    fl_modules["channels"].getChannelVolume.assert_called_once_with(2, mode=True)


def test_call_turns_results_that_are_not_json_into_text(controller, fl_modules):
    fl_modules["general"].getVersion.return_value = object()

    result = controller.dispatch_command(
        "api.call", {"module": "general", "function": "getVersion"}
    )

    assert isinstance(result["result"], str)


@pytest.mark.parametrize("params", [
    {"module": "os", "function": "getcwd"},
    {"module": "mixer", "function": "_private"},
    {"module": "mixer", "function": "__class__"},
    {"module": "mixer", "function": "getTrackName", "args": "3"},
    {"module": "mixer", "function": "getTrackName", "kwargs": [1]},
    {"module": "mixer"},
])
def test_call_refuses_other_modules_private_names_and_bad_arguments(controller, params):
    assert "error" in controller.dispatch_command("api.call", params)


def test_call_reports_what_fl_raised(controller, fl_modules):
    fl_modules["mixer"].getTrackName.side_effect = TypeError("bad index")

    result = controller.dispatch_command(
        "api.call", {"module": "mixer", "function": "getTrackName", "args": [999]}
    )

    assert "bad index" in result["error"]


def test_call_reports_a_module_fl_does_not_have(controller, monkeypatch):
    monkeypatch.setattr(controller, "_api_module", lambda name: None)

    result = controller.dispatch_command("api.call", {"module": "ilmcp", "function": "addChannel"})

    assert "not available" in result["error"]


def test_list_names_a_modules_public_functions(controller, monkeypatch):
    fake = types.SimpleNamespace(getTrackName=lambda i: "", trackCount=lambda: 1, _hidden=1,
                                 VERSION=3)
    monkeypatch.setattr(controller, "_api_module", lambda name: fake)

    result = controller.dispatch_command("api.list", {"module": "mixer"})

    assert result == {"module": "mixer", "functions": ["getTrackName", "trackCount"],
                      "constants": {"VERSION": 3}}


def test_list_filters_by_text(controller, monkeypatch):
    fake = types.SimpleNamespace(getEqGain=lambda: 0, setEqGain=lambda: 0, trackCount=lambda: 1)
    monkeypatch.setattr(controller, "_api_module", lambda name: fake)

    result = controller.dispatch_command("api.list", {"module": "mixer", "search": "eq"})

    assert result["functions"] == ["getEqGain", "setEqGain"]
