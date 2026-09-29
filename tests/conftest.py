"""Shared fixtures: load the FL-side scripts against fake FL API modules."""

from __future__ import annotations

import sys
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path
from types import ModuleType

import pytest

from tests.fakes import make_controller_modules, make_flpianoroll

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROLLER_PATH = REPO_ROOT / "fl_controller" / "device_FLStudioMCP.py"
PIANO_ROLL_SCRIPT_PATH = REPO_ROOT / "scripts" / "ComposeWithLLM.pyscript"


def _load_source(name: str, path: Path) -> ModuleType:
    loader = SourceFileLoader(name, str(path))
    spec = spec_from_loader(name, loader)
    module = module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def fl_modules(monkeypatch: pytest.MonkeyPatch) -> dict:
    modules = make_controller_modules()
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return modules


@pytest.fixture
def controller(fl_modules: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """The FL controller script with its JSON files redirected to tmp_path."""
    module = _load_source("device_FLStudioMCP", CONTROLLER_PATH)
    monkeypatch.setattr(module, "COMMAND_FILE", tmp_path / "mcp_command.json")
    monkeypatch.setattr(module, "RESPONSE_FILE", tmp_path / "mcp_response.json")
    return module


@pytest.fixture
def flp(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = make_flpianoroll()
    monkeypatch.setitem(sys.modules, "flpianoroll", module)
    return module


@pytest.fixture
def piano_roll_script(flp: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The ComposeWithLLM piano roll script with its JSON files in tmp_path."""
    module = _load_source("ComposeWithLLM", PIANO_ROLL_SCRIPT_PATH)
    monkeypatch.setattr(module, "REQUEST_FILE", str(tmp_path / "mcp_request.json"))
    monkeypatch.setattr(module, "RESPONSE_FILE", str(tmp_path / "mcp_response.json"))
    monkeypatch.setattr(module, "STATE_FILE", str(tmp_path / "piano_roll_state.json"))
    return module


@pytest.fixture
def settings_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the server at a temporary FL Studio Settings folder."""
    settings = tmp_path / "Settings"
    monkeypatch.setenv("FL_MCP_SETTINGS_DIR", str(settings))
    return settings
