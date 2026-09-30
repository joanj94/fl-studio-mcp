"""Style packs: optional, genre-specific data kept out of the code.

A pack is a JSON file describing a musical style in the toolkit's own
vocabulary: tempo range, scales, the roles (channels) it needs, song structure,
example rhythm grids and chord progressions, and free-text guidance. Packs are
checked against that vocabulary, so a pack can't suggest something the music
tools would reject.

Built-in packs live in `styles/` next to this module. Set FL_MCP_STYLES_DIR to
a folder of your own packs; a user pack with the same name replaces the
built-in one. Packs are read from disk on every call, so edits apply at once.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from fl_studio_mcp.music.rhythm import parse_grid
from fl_studio_mcp.music.theory import progression, scale_intervals
from fl_studio_mcp.utils.roles import normalize

BUILTIN_DIR = Path(__file__).resolve().parent / "styles"
USER_DIR_ENV = "FL_MCP_STYLES_DIR"

MIN_TEMPO = 10
MAX_TEMPO = 522
# Any key works: progressions are only checked for being resolvable.
CHECK_KEY = "C"

FIELDS = (
    "name", "title", "description", "tempo", "time_signature", "scales",
    "roles", "structure", "rhythms", "progressions", "guidance",
)
_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TIME_SIGNATURE = re.compile(r"^[1-9][0-9]?/(1|2|4|8|16|32)$")


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} must be non-empty text")
    return value


def _texts(value: Any, where: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list of text")
    return [_text(item, f"{where}[{i}]") for i, item in enumerate(value)]


def _items(value: Any, where: str, keys: tuple[str, ...]) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(f"{where} must be a list")
    for i, item in enumerate(value):
        if not isinstance(item, dict) or set(item) != set(keys):
            raise ValueError(f"{where}[{i}] must have exactly the fields {list(keys)}")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _check_tempo(tempo: Any) -> None:
    if not isinstance(tempo, dict) or set(tempo) != {"min", "max", "typical"}:
        raise ValueError("tempo must have min, max and typical")
    if not all(_is_number(v) for v in tempo.values()):
        raise ValueError("tempo values must be numbers")
    if not MIN_TEMPO <= tempo["min"] <= tempo["typical"] <= tempo["max"] <= MAX_TEMPO:
        raise ValueError(
            f"tempo must satisfy {MIN_TEMPO} <= min <= typical <= max <= {MAX_TEMPO}, got {tempo}"
        )


def _check_roles(roles: Any) -> set[str]:
    """Validate the roles block and return the normalized role names."""
    if not isinstance(roles, dict) or set(roles) != {"required", "optional"}:
        raise ValueError("roles must have required and optional lists")
    required = _texts(roles["required"], "roles.required")
    if not required:
        raise ValueError("roles.required must name at least one role")
    names: set[str] = set()
    for role in required + _texts(roles["optional"], "roles.optional"):
        key = normalize(role)
        if not key:
            raise ValueError(f"Role {role!r} must contain letters or digits")
        if key in names:
            raise ValueError(f"Role {role!r} is listed twice")
        names.add(key)
    return names


def _check_structure(structure: Any) -> None:
    for i, part in enumerate(_items(structure, "structure", ("section", "bars", "description"))):
        _text(part["section"], f"structure[{i}].section")
        bars = part["bars"]
        if not isinstance(bars, int) or isinstance(bars, bool) or bars < 1:
            raise ValueError(f"structure[{i}].bars must be a whole number of 1 or more")


def _check_rhythms(rhythms: Any, roles: set[str]) -> None:
    keys = ("name", "role", "grid", "description")
    for i, rhythm in enumerate(_items(rhythms, "rhythms", keys)):
        name = _text(rhythm["name"], f"rhythms[{i}].name")
        if normalize(_text(rhythm["role"], f"rhythms[{i}].role")) not in roles:
            raise ValueError(f"Rhythm {name!r} uses role {rhythm['role']!r}, not listed in roles")
        try:
            _, steps = parse_grid(_text(rhythm["grid"], f"rhythms[{i}].grid"))
        except ValueError as e:
            raise ValueError(f"Rhythm {name!r} grid: {e}") from e
        if steps < 1:
            raise ValueError(f"Rhythm {name!r} grid has no steps")


def _check_progressions(progressions: Any) -> None:
    keys = ("chords", "scale", "description")
    for i, entry in enumerate(_items(progressions, "progressions", keys)):
        chords = _texts(entry["chords"], f"progressions[{i}].chords")
        if not chords:
            raise ValueError(f"progressions[{i}].chords must name at least one chord")
        scale = _text(entry["scale"], f"progressions[{i}].scale")
        try:
            progression(chords, key=CHECK_KEY, scale=scale)
        except ValueError as e:
            raise ValueError(f"progressions[{i}] {chords}: {e}") from e


def validate_pack(pack: Any) -> dict[str, Any]:
    """Return the pack unchanged if valid; raise ValueError naming the first problem."""
    if not isinstance(pack, dict):
        raise ValueError("A style pack must be a JSON object")
    unknown = sorted(set(pack) - set(FIELDS))
    missing = [field for field in FIELDS if field not in pack]
    if unknown:
        raise ValueError(f"Unknown field(s) {unknown}; allowed: {list(FIELDS)}")
    if missing:
        raise ValueError(f"Missing field(s) {missing}")
    if not isinstance(pack["name"], str) or not _NAME.match(pack["name"]):
        raise ValueError("name must be lowercase words joined by '-', e.g. 'my-style'")
    _text(pack["title"], "title")
    _text(pack["description"], "description")
    _check_tempo(pack["tempo"])
    if not isinstance(pack["time_signature"], str) or not _TIME_SIGNATURE.match(
        pack["time_signature"]
    ):
        raise ValueError("time_signature must look like '4/4' or '6/8'")
    for scale in _texts(pack["scales"], "scales"):
        scale_intervals(scale)
    roles = _check_roles(pack["roles"])
    _check_structure(pack["structure"])
    _check_rhythms(pack["rhythms"], roles)
    _check_progressions(pack["progressions"])
    _texts(pack["guidance"], "guidance")
    return pack


def user_dir() -> Path | None:
    value = os.environ.get(USER_DIR_ENV)
    return Path(value) if value else None


def _pack_files() -> dict[str, Path]:
    """Pack files by name; user packs replace built-in ones."""
    files = {path.stem: path for path in sorted(BUILTIN_DIR.glob("*.json"))}
    extra = user_dir()
    if extra is not None and extra.is_dir():
        files.update({path.stem: path for path in sorted(extra.glob("*.json"))})
    return files


def _load(path: Path) -> dict[str, Any]:
    try:
        pack = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"Can't read {path.name}: {e}") from e
    validate_pack(pack)
    if pack["name"] != path.stem:
        raise ValueError(
            f"{path.name} holds style {pack['name']!r}; the name must match the file name "
            f"({path.stem!r})"
        )
    return pack


def list_packs() -> dict[str, list]:
    """All valid packs, plus the files that failed to load and why."""
    styles, invalid = [], []
    for path in _pack_files().values():
        try:
            styles.append(_load(path))
        except ValueError as e:
            invalid.append({"file": path.name, "error": str(e)})
    return {"styles": styles, "invalid": invalid}


def get_pack(name: str) -> dict[str, Any]:
    """One pack by name. Raises ValueError for unknown or invalid packs."""
    files = _pack_files()
    if name not in files:
        raise ValueError(f"Unknown style {name!r}. Available: {sorted(files)}")
    return _load(files[name])
