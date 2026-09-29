"""Style packs: optional genre data, validated against the music toolkit's vocabulary."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

import pytest

from fl_studio_mcp import style_packs
from fl_studio_mcp.style_packs import BUILTIN_DIR, get_pack, list_packs, validate_pack
from fl_studio_mcp.tools import register_style_tools
from fl_studio_mcp.utils import connection
from tests.fakes import ScriptedConnection, ToolCollector

SRC = Path(__file__).resolve().parents[1] / "src" / "fl_studio_mcp"

VALID: dict[str, Any] = {
    "name": "test-style",
    "title": "Test Style",
    "description": "A style used by the tests.",
    "tempo": {"min": 100, "max": 120, "typical": 110},
    "time_signature": "4/4",
    "scales": ["minor", "dorian"],
    "roles": {"required": ["kick", "bass"], "optional": ["lead"]},
    "structure": [
        {"section": "intro", "bars": 8, "description": "Drums only."},
        {"section": "main", "bars": 16, "description": "Everything."},
    ],
    "rhythms": [
        {"name": "four on the floor", "role": "kick", "grid": "X...X...X...X...",
         "description": "Kick on every beat."},
    ],
    "progressions": [
        {"chords": ["i", "VI", "III", "VII"], "scale": "minor", "description": "Loop."},
    ],
    "guidance": ["Keep it simple."],
}


def _pack(**changes: Any) -> dict[str, Any]:
    pack = copy.deepcopy(VALID)
    pack.update(changes)
    return pack


def test_valid_pack_passes():
    assert validate_pack(_pack()) == _pack()


@pytest.mark.parametrize("changes, message", [
    ({"name": "Bad Name"}, "name"),
    ({"title": ""}, "title"),
    ({"tempo": {"min": 120, "max": 100, "typical": 110}}, "tempo"),
    ({"tempo": {"min": 5, "max": 100, "typical": 50}}, "tempo"),
    ({"tempo": {"min": 100, "max": 120}}, "typical"),
    ({"time_signature": "4-4"}, "time_signature"),
    ({"scales": ["minor", "lydian_sharp_eleven"]}, "lydian_sharp_eleven"),
    ({"roles": {"required": [], "optional": ["lead"]}}, "required"),
    ({"roles": {"required": ["kick", "Kick"], "optional": []}}, "twice"),
    ({"structure": [{"section": "intro", "bars": 0, "description": ""}]}, "bars"),
    ({"rhythms": [{"name": "r", "role": "kick", "grid": "Xq..", "description": ""}]}, "grid"),
    ({"rhythms": [{"name": "r", "role": "vocal", "grid": "X...", "description": ""}]}, "vocal"),
    ({"progressions": [{"chords": ["i", "Q7"], "scale": "minor", "description": ""}]}, "Q7"),
    ({"progressions": [{"chords": ["i"], "scale": "nope", "description": ""}]}, "nope"),
    ({"progressions": [{"chords": [], "scale": "minor", "description": ""}]}, "chords"),
    ({"progressions": [{"chords": ["i"], "scale": "", "description": ""}]},
     r"progressions\[0\]\.scale"),
    ({"guidance": "not a list"}, "guidance"),
    ({"unknown_field": 1}, "unknown_field"),
])
def test_invalid_packs_are_rejected_with_the_problem_named(changes, message):
    with pytest.raises(ValueError, match=message):
        validate_pack(_pack(**changes))


def test_missing_field_is_rejected():
    pack = _pack()
    del pack["roles"]
    with pytest.raises(ValueError, match="roles"):
        validate_pack(pack)


def test_builtin_packs_are_valid_and_named_after_their_files():
    files = sorted(BUILTIN_DIR.glob("*.json"))
    assert len(files) >= 3
    for path in files:
        pack = validate_pack(json.loads(path.read_text(encoding="utf-8")))
        assert pack["name"] == path.stem


def test_no_style_name_appears_in_the_code():
    """Genre knowledge lives in the packs, never in the Python code."""
    names = set()
    for path in BUILTIN_DIR.glob("*.json"):
        pack = json.loads(path.read_text(encoding="utf-8"))
        names |= {pack["name"].lower(), pack["title"].lower()}
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        found = [n for n in names if re.search(rf"\b{re.escape(n)}\b", text)]
        assert not found, f"{path.name} mentions style(s) {found}"


@pytest.fixture
def user_dir(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("FL_MCP_STYLES_DIR", str(tmp_path))
    return tmp_path


def _write(directory: Path, pack: dict[str, Any], stem: str | None = None) -> None:
    (directory / f"{stem or pack['name']}.json").write_text(json.dumps(pack), encoding="utf-8")


def test_user_packs_are_added_and_override_builtins(user_dir):
    builtin = sorted(BUILTIN_DIR.glob("*.json"))[0].stem
    _write(user_dir, _pack())
    _write(user_dir, _pack(name=builtin, title="My Own"))

    result = list_packs()

    names = [s["name"] for s in result["styles"]]
    assert "test-style" in names
    assert get_pack(builtin)["title"] == "My Own"
    assert result["invalid"] == []


def test_invalid_user_packs_are_reported_not_fatal(user_dir):
    _write(user_dir, _pack(scales=["nope"]), stem="broken")
    (user_dir / "garbage.json").write_text("{not json", encoding="utf-8")
    _write(user_dir, _pack(), stem="wrong-name")

    invalid = {entry["file"]: entry["error"] for entry in list_packs()["invalid"]}

    assert set(invalid) == {"broken.json", "garbage.json", "wrong-name.json"}
    assert "nope" in invalid["broken.json"]
    assert "wrong-name" in invalid["wrong-name.json"]
    assert list_packs()["styles"]  # the built-in packs still load


def test_get_unknown_pack_lists_the_known_ones(user_dir):
    _write(user_dir, _pack())
    with pytest.raises(ValueError, match="test-style"):
        get_pack("does-not-exist")


def test_missing_user_dir_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setenv("FL_MCP_STYLES_DIR", str(tmp_path / "missing"))
    assert list_packs()["invalid"] == []


def test_packs_are_read_fresh_each_time(user_dir):
    _write(user_dir, _pack())
    assert get_pack("test-style")["title"] == "Test Style"
    _write(user_dir, _pack(title="Edited"))
    assert get_pack("test-style")["title"] == "Edited"


CHANNELS = [
    {"index": 0, "name": "Kick"},
    {"index": 1, "name": "Sub Bass"},
]


@pytest.fixture
def fl(monkeypatch) -> ScriptedConnection:
    conn = ScriptedConnection({
        "channels.getAll": {"channels": CHANNELS},
        "project.getTempo": {"bpm": 115.0},
    })
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    return conn


@pytest.fixture
def tools(fl, user_dir) -> dict[str, Any]:
    _write(user_dir, _pack())
    collector = ToolCollector()
    register_style_tools(collector)
    return collector.tools


def test_style_list_summarises_packs(tools):
    styles = {s["name"]: s for s in tools["style_list"]()["styles"]}

    assert styles["test-style"] == {
        "name": "test-style",
        "title": "Test Style",
        "description": "A style used by the tests.",
        "tempo": {"min": 100, "max": 120, "typical": 110},
    }


def test_style_get_returns_the_pack_or_an_error(tools):
    assert tools["style_get"]("test-style")["roles"]["required"] == ["kick", "bass"]
    assert "Unknown style" in tools["style_get"]("nope")["error"]


def test_style_check_project_compares_tempo_and_roles(tools, fl):
    result = tools["style_check_project"]("test-style")

    assert result["ok"] is True
    assert result["tempo"] == {"bpm": 115.0, "min": 100, "max": 120, "ok": True}
    assert result["required_roles"]["found"]["bass"] == {"channel": 1, "name": "Sub Bass"}
    assert result["optional_roles"]["missing"] == ["lead"]


def test_style_check_project_flags_tempo_and_missing_roles(tools, fl):
    fl.results["project.getTempo"] = {"bpm": 150.0}
    fl.results["channels.getAll"] = {"channels": [{"index": 0, "name": "Kick"}]}

    result = tools["style_check_project"]("test-style")

    assert result["ok"] is False
    assert result["tempo"]["ok"] is False
    assert result["required_roles"]["missing"] == ["bass"]


def test_style_check_project_reports_fl_errors(tools, fl):
    fl.results["project.getTempo"] = {"error": "not connected"}

    assert tools["style_check_project"]("test-style") == {"error": "not connected"}


def test_style_check_project_unknown_style(tools, fl):
    assert "Unknown style" in tools["style_check_project"]("nope")["error"]
    assert fl.sent == []


def test_style_list_reports_where_packs_live(tools, user_dir):
    result = tools["style_list"]()

    assert result["builtin_dir"] == str(style_packs.BUILTIN_DIR)
    assert result["user_dir"] == str(user_dir)
