"""Locating FL Studio's Settings folder and writing the JSON files shared with FL.

The FL-side scripts (fl_controller/device_FLStudioMCP.py and
scripts/ComposeWithLLM.pyscript) resolve the same folder independently; keep
their Documents lookup in sync with this module.
"""

from __future__ import annotations

import json
import os
import platform
import time
from pathlib import Path
from typing import Any

SETTINGS_DIR_ENV = "FL_MCP_SETTINGS_DIR"

# Replacing a file fails on Windows while the other side holds it open for a read.
REPLACE_ATTEMPTS = 5
REPLACE_RETRY_DELAY = 0.02  # seconds

# CSIDL_PERSONAL: the user's Documents folder, honouring OneDrive/folder redirection.
_CSIDL_PERSONAL = 5
_MAX_PATH = 260


def _windows_documents_dir() -> Path | None:
    """Ask the Windows shell for the real Documents folder, or None on failure."""
    try:
        import ctypes

        buffer = ctypes.create_unicode_buffer(_MAX_PATH)
        result = ctypes.windll.shell32.SHGetFolderPathW(None, _CSIDL_PERSONAL, None, 0, buffer)
    except (AttributeError, OSError):
        return None
    if result != 0 or not buffer.value:
        return None
    return Path(buffer.value)


def get_documents_dir() -> Path:
    """The user's Documents folder (redirection-aware on Windows)."""
    if platform.system() == "Windows":
        resolved = _windows_documents_dir()
        if resolved is not None:
            return resolved
    return Path.home() / "Documents"


def get_fl_settings_dir() -> Path:
    """FL Studio's Settings folder; FL_MCP_SETTINGS_DIR overrides the default location.

    The override is for a custom FL "User data folder". The FL-side scripts don't
    see this variable, so they must be able to find the same folder on their own.
    """
    override = os.environ.get(SETTINGS_DIR_ENV)
    if override:
        return Path(override)
    return get_documents_dir() / "Image-Line" / "FL Studio" / "Settings"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_hardware_dir() -> Path:
    """Folder holding the MIDI controller script and its command/response files."""
    return _ensure_dir(get_fl_settings_dir() / "Hardware" / "FLStudioMCP")


def get_piano_roll_scripts_dir() -> Path:
    """Folder holding the piano roll script and its request/response/state files."""
    return _ensure_dir(get_fl_settings_dir() / "Piano roll scripts")


def _replace_with_retry(src: Path, dst: Path) -> None:
    """os.replace, retried: on Windows it fails while another process reads dst."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(REPLACE_RETRY_DELAY)


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON so a concurrent reader sees either the old or the new file, never half."""
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _replace_with_retry(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
