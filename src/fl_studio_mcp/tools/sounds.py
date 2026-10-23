"""Finding samples by how they sound, and listing the effects FL can load.

`fl_browser_similar` measures the audio files below a browser folder
(`audio/sound_features.py`) and returns the ones nearest to a reference. Features
are kept in a cache (`<Hardware>/FLStudioMCP/sound_features.json`, keyed by the
file's path, size and time), so a folder is measured once; a call measures for at
most `max_seconds` and says when files are left for the next call.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.audio import MISSING_NUMPY
from fl_studio_mcp.utils import browser_index
from fl_studio_mcp.utils.paths import atomic_write_json, get_fl_settings_dir, get_hardware_dir
from fl_studio_mcp.utils.plugin_database import get_effects_dir

if TYPE_CHECKING:
    from fastmcp import FastMCP

logger = logging.getLogger(__name__)

AUDIO_SUFFIXES = (".wav", ".mp3", ".ogg", ".flac", ".aif", ".aiff")
CACHE_FILE = "sound_features.json"
CACHE_VERSION = 1
MAX_LIMIT = 100


def _roots() -> list[tuple[str, Path]]:
    from fl_studio_mcp.utils import fl_windows

    try:
        install_dir = fl_windows.fl_install_dir()
    except Exception:  # not on Windows, or FL isn't running: the user's folders remain
        install_dir = None
    return browser_index.browser_roots(install_dir, get_fl_settings_dir().parent)


def _cache_path() -> Path:
    return get_hardware_dir() / CACHE_FILE


def load_cache() -> dict:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("files", {}) if data.get("version") == CACHE_VERSION else {}


def save_cache(files: dict) -> None:
    path = _cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, {"version": CACHE_VERSION, "files": files})


def _stamp(path: Path) -> list:
    info = path.stat()
    return [info.st_size, int(info.st_mtime)]


def measure(path: Path) -> list[float] | None:
    """Feature vector of one file (plain WAVs directly, others through ffmpeg)."""
    from fl_studio_mcp.audio.sound_features import features
    from fl_studio_mcp.audio.wav import read_wav

    try:
        samples, rate = read_wav(path)
    except ValueError:  # e.g. OGG data in a .wav container, or an MP3
        from fl_studio_mcp.audio.decode import load_audio

        samples, rate = load_audio(path)
    return features(samples, rate)


def cached_features(files: list[tuple[str, Path]], cache: dict,
                    max_seconds: float) -> tuple[dict[str, list[float]], int, int]:
    """({browser path: vector}, files left unmeasured, files that couldn't be read).

    New and changed files are measured into `cache` until `max_seconds` have passed.
    """
    started = time.monotonic()
    found, left, failed = {}, 0, 0
    for browser_path, disk in files:
        key = str(disk)
        try:
            stamp = _stamp(disk)
        except OSError:
            continue
        entry = cache.get(key)
        if entry is None or entry.get("stamp") != stamp:
            if time.monotonic() - started > max_seconds:
                left += 1
                continue
            try:
                vector = measure(disk)
            except (OSError, ValueError, MemoryError) as e:
                logger.debug("Could not measure %s: %s", disk, e)
                vector = None
            entry = cache[key] = {"stamp": stamp, "features": vector}
        if entry["features"] is None:
            failed += 1
        else:
            found[browser_path] = entry["features"]
    return found, left, failed


def find_similar(path: str, folder: str = "", limit: int = 10,
                 max_seconds: float = 60.0) -> dict:
    """The tool's work; raises ValueError/OSError."""
    if isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be 1-{MAX_LIMIT}, got {limit!r}")
    if not 0 < max_seconds <= 600:
        raise ValueError(f"max_seconds must be more than 0 and at most 600, got {max_seconds}")
    from fl_studio_mcp.audio.sound_features import describe, nearest

    roots = _roots()
    given = Path(path)
    reference_disk = given if given.is_absolute() else browser_index.disk_path(roots, path)
    if reference_disk is None or not reference_disk.is_file():
        raise ValueError(f"{path} is neither a file on disk nor a file in a browser folder "
                         "this server can see (fl_browser_search finds those)")
    reference = measure(reference_disk)
    if reference is None:
        raise ValueError(f"{path} is silent")
    files = [(browser, disk) for browser, disk in
             browser_index.files_on_disk(roots, folder, AUDIO_SUFFIXES)
             if disk.resolve() != reference_disk.resolve()]
    if not files:
        raise ValueError(f"No audio files below the browser folder {folder or '(all)'!r}")
    cache = load_cache()
    found, left, failed = cached_features(files, cache, max_seconds)
    save_cache(cache)
    result: dict = {
        "reference": {"path": path, **describe(reference)},
        "matches": [{"path": name, "distance": distance, **describe(found[name])}
                    for name, distance in nearest(reference, found, limit)],
        "compared": len(found),
    }
    if failed:
        result["unreadable"] = failed
    if left:
        result["warning"] = (f"{left} files are not measured yet (max_seconds ran out); "
                             "call again to measure more: they are kept")
    return result


def list_effects() -> list[dict]:
    root = get_effects_dir()
    if not root.is_dir():
        raise ValueError(f"FL Studio's plugin database was not found at {root}")
    return sorted(({"name": path.stem, "category": path.parent.name,
                    "path": f"Plugin database/Effects/{path.parent.name}/{path.stem}"}
                   for path in root.glob("*/*.fst")), key=lambda e: e["name"].lower())


def register_sound_tools(mcp: FastMCP) -> None:
    """Register the sound search tools with the MCP server."""

    @mcp.tool()
    def fl_browser_similar(path: str, folder: str = "", limit: int = 10,
                           max_seconds: float = 60.0) -> dict:
        """Find samples that sound like a given one, whatever their names: other
        kicks like this kick, hats like this hat, a darker or shorter version.

        Compares length, brightness, the energy in six bands (sub to air), attack,
        decay and noisiness. The first search in a folder measures its files (plain
        WAVs quickly; FL's own packs need ffmpeg and go slower); they are kept, so
        later searches are fast. Doesn't touch FL Studio.

        Args:
            path: The reference: a browser path (from fl_browser_search) or a file on disk.
            folder: Browser folder to search below, e.g. "Packs/Drums" or "Project
                bones". Empty searches every folder (slow the first time).
            limit: How many matches (1-100).
            max_seconds: Most time spent measuring new files in this call; call again
                when the result has a "warning" about files left.

        Returns {"reference": {...}, "matches": [{"path", "distance", "seconds",
        "brightness_hz", "bands", "attack_ms", "decay_ms", "noisiness"}], "compared",
        "warning"?} or {"error"}. Smaller distance = more alike. The paths go into
        fl_load_in_new_channel or fl_load_in_channel.
        """
        try:
            return find_similar(path, folder, limit, max_seconds)
        except ImportError:
            return {"error": MISSING_NUMPY}
        except (ValueError, OSError) as e:
            return {"error": str(e)}

    @mcp.tool()
    def fl_list_effects(category: str = "") -> dict:
        """List the effect plugins fl_add_effect can load, from FL's plugin database
        (what the plugin picker shows), optionally of one category ("Filter",
        "Dynamics", ...; any case). Returns {"effects": [{"name", "category",
        "path"}], "categories"} or {"error"}."""
        try:
            effects = list_effects()
        except ValueError as e:
            return {"error": str(e)}
        categories = sorted({e["category"] for e in effects}, key=str.lower)
        if category:
            effects = [e for e in effects if e["category"].lower() == category.lower()]
            if not effects:
                return {"error": f"No effect category {category!r}; there are "
                                 f"{', '.join(categories)}"}
        return {"effects": effects, "categories": categories}
