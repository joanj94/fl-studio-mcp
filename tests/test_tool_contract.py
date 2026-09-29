"""Contract between server tools and the FL controller script.

Every MIDI-channel tool is called against the real controller dispatch
(in-process, FL API faked). A tool whose action string the controller doesn't
know, or that crashes on a well-formed result, fails here.
"""

from __future__ import annotations

import inspect
import sys
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from fl_studio_mcp.tools import (
    register_channel_tools,
    register_mixer_tools,
    register_pattern_tools,
    register_plugin_tools,
    register_project_tools,
    register_roles_tools,
    register_transport_tools,
)
from tests.fakes import ToolCollector

REGISTRARS = (
    register_transport_tools,
    register_mixer_tools,
    register_channel_tools,
    register_plugin_tools,
    register_project_tools,
    register_pattern_tools,
    register_roles_tools,
)

# Sample values for required parameters, by annotation (annotations are strings
# because the tool modules use `from __future__ import annotations`).
SAMPLES: dict[str, Any] = {
    "int": 1,
    "float": 0.5,
    "bool": True,
    "str": "Lead",
    "int | str": 1,
    "list[str]": ["Name"],
    "list[int]": [1, 0, 1, 0],
    "list[bool]": [True, False, True, False],
    "list[dict]": [{"step": 0, "on": True, "velocity": 1.0}],
}

# Parameters whose valid values are restricted, by parameter name.
NAMED_SAMPLES: dict[str, Any] = {"mode": "song", "bpm": 140.0}


class NumericFLModule(MagicMock):
    """FL API module fake whose getters return plausible types: 1, or a string for names."""

    def _get_child_mock(self, **kwargs: Any) -> MagicMock:
        returns_text = str(kwargs.get("name", "")).endswith("Name")
        return MagicMock(return_value="Name" if returns_text else 1)


class LoopbackConnection:
    """Routes send_command straight into the controller's dispatch_command."""

    def __init__(self, controller) -> None:
        self.controller = controller
        self.calls: list[tuple[str, dict]] = []

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        result = self.controller.dispatch_command(action, params or {})
        self.calls.append((action, result))
        return {"success": "error" not in result, **result}


def _collect_tools() -> dict[str, Callable]:
    collector = ToolCollector()
    for register in REGISTRARS:
        register(collector)
    return collector.tools


def _sample_args(fn: Callable) -> dict[str, Any]:
    args = {}
    for name, param in inspect.signature(fn).parameters.items():
        if param.default is inspect.Parameter.empty:
            args[name] = NAMED_SAMPLES.get(name, SAMPLES[str(param.annotation)])
    return args


TOOL_NAMES = sorted(_collect_tools())


@pytest.fixture
def numeric_fl(fl_modules, monkeypatch) -> None:
    """Swap the controller's FL modules for fakes returning numbers."""
    for name in fl_modules:
        monkeypatch.setitem(sys.modules, name, NumericFLModule(name=name))


@pytest.fixture
def loopback(numeric_fl, controller, monkeypatch) -> LoopbackConnection:
    connection = LoopbackConnection(controller)
    monkeypatch.setattr("fl_studio_mcp.utils.connection.get_connection", lambda: connection)
    return connection


def test_tools_were_discovered():
    assert len(TOOL_NAMES) > 30


@pytest.mark.parametrize("tool_name", TOOL_NAMES)
def test_tool_actions_are_known_to_controller(tool_name, loopback):
    tool = _collect_tools()[tool_name]  # collected after get_connection is patched

    tool(**_sample_args(tool))

    assert loopback.calls, f"{tool_name} sent no command"
    unknown = [a for a, r in loopback.calls if "Unknown action" in str(r.get("error", ""))]
    assert not unknown, f"{tool_name} sent actions the controller doesn't handle: {unknown}"
