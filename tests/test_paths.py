"""Locating FL Studio's Settings folder and writing JSON safely."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fl_studio_mcp.utils import paths


def test_env_override_wins(settings_dir: Path):
    assert paths.get_fl_settings_dir() == settings_dir


def test_windows_uses_known_documents_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("FL_MCP_SETTINGS_DIR", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")
    monkeypatch.setattr(paths, "_windows_documents_dir", lambda: tmp_path / "OneDrive" / "Docs")

    expected = tmp_path / "OneDrive" / "Docs" / "Image-Line" / "FL Studio" / "Settings"
    assert paths.get_fl_settings_dir() == expected


def test_windows_falls_back_to_home_documents(monkeypatch):
    monkeypatch.delenv("FL_MCP_SETTINGS_DIR", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Windows")
    monkeypatch.setattr(paths, "_windows_documents_dir", lambda: None)

    assert paths.get_documents_dir() == Path.home() / "Documents"


def test_macos_uses_home_documents(monkeypatch):
    monkeypatch.delenv("FL_MCP_SETTINGS_DIR", raising=False)
    monkeypatch.setattr(paths.platform, "system", lambda: "Darwin")

    assert paths.get_documents_dir() == Path.home() / "Documents"


def test_hardware_and_piano_roll_dirs_are_created(settings_dir: Path):
    assert paths.get_hardware_dir() == settings_dir / "Hardware" / "FLStudioMCP"
    assert paths.get_piano_roll_scripts_dir() == settings_dir / "Piano roll scripts"
    assert paths.get_hardware_dir().is_dir()
    assert paths.get_piano_roll_scripts_dir().is_dir()


def _flaky_replace(failures: int, real_replace):
    """os.replace that raises PermissionError `failures` times, as on Windows while
    another process has the destination open for reading."""
    calls = {"n": 0}

    def replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    return replace


def test_atomic_write_retries_when_destination_is_locked(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths.os, "replace", _flaky_replace(2, os.replace))
    target = tmp_path / "out.json"

    paths.atomic_write_json(target, {"a": 1})

    assert json.loads(target.read_text()) == {"a": 1}


def test_atomic_write_gives_up_and_cleans_temp(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(paths.os, "replace", _flaky_replace(99, os.replace))
    monkeypatch.setattr(paths, "REPLACE_RETRY_DELAY", 0)

    with pytest.raises(PermissionError):
        paths.atomic_write_json(tmp_path / "out.json", {"a": 1})

    assert list(tmp_path.iterdir()) == []


def test_atomic_write_json_writes_and_leaves_no_temp(tmp_path: Path):
    target = tmp_path / "out.json"

    paths.atomic_write_json(target, {"a": 1})
    paths.atomic_write_json(target, {"a": 2})

    assert json.loads(target.read_text()) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["out.json"]
