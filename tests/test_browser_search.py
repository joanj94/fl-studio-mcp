"""Finding sounds by name in the folders FL's browser takes from disk."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fl_studio_mcp.tools import register_browser_tools
from fl_studio_mcp.utils import browser_index, fl_windows
from tests.fakes import ToolCollector


def _files(root: Path, *paths: str) -> None:
    for path in paths:
        file = root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"")


@pytest.fixture
def disk(tmp_path) -> tuple[Path, Path]:
    """FL's install folder and the user data folder, with a few sounds in each."""
    install, user = tmp_path / "FL", tmp_path / "User"
    _files(install / "Data" / "Patches",
           "Packs/Drums/Kicks/909 Kick.wav", "Packs/Drums/Kicks/Grv Kick 27.wav",
           "Packs/Drums/Kicks/readme.txt", "Packs/Drums/Hats/909 CH 1.wav",
           "Packs/Risers/Riser Noise.wv", "Packs.nfo",
           "Plugin presets/Generators/Sytrus/Pad/Dark heaven.fst",
           "Plugin presets/Generators/Sytrus/Drum/Kick.fst",
           "Plugin presets/Generators/Morphine/Leads/LED Scream MC.mrp")
    _files(user, "Presets/Plugin presets/Generators/Sytrus/My dark lead.fst",
           "Projects/Project bones/Hardcore Kick 040 F.wav", "Settings/unrelated.wav")
    return install, user


def _search(disk, query: str, **kwargs) -> tuple[list[str], int]:
    return browser_index.search(browser_index.browser_roots(*disk), query, **kwargs)


def test_every_word_must_be_in_the_path_in_any_case_and_order(disk):
    assert _search(disk, "KICK 909") == (["Packs/Drums/Kicks/909 Kick.wav"], 1)
    assert _search(disk, "sytrus dark")[0] == [
        "Plugin presets/Generators/Sytrus/My dark lead.fst",
        "Plugin presets/Generators/Sytrus/Pad/Dark heaven.fst",
    ]


def test_files_named_by_the_words_come_before_files_only_in_such_a_folder(disk):
    matches, total = _search(disk, "kick")

    assert total == 4
    assert matches == [
        "Packs/Drums/Kicks/909 Kick.wav",
        "Packs/Drums/Kicks/Grv Kick 27.wav",
        "Project bones/Hardcore Kick 040 F.wav",
        "Plugin presets/Generators/Sytrus/Drum/Kick.fst",
    ]


def test_only_files_that_can_be_loaded_are_found(disk):
    assert _search(disk, "readme") == ([], 0)
    assert _search(disk, "unrelated") == ([], 0)  # Settings isn't a browser folder
    assert _search(disk, "riser") == (["Packs/Risers/Riser Noise.wv"], 1)


def test_the_search_can_be_kept_to_a_folder_and_cut_short(disk):
    assert _search(disk, "909", folder="Packs/Drums/Hats") == (
        ["Packs/Drums/Hats/909 CH 1.wav"], 1)
    assert _search(disk, "9", folder="packs\\drums\\") [1] == 2
    assert _search(disk, "kick", folder="Pack") == ([], 0)  # a folder's whole name
    assert _search(disk, "kick", limit=1) == (["Packs/Drums/Kicks/909 Kick.wav"], 4)


def test_an_empty_query_is_refused(disk):
    with pytest.raises(ValueError):
        _search(disk, "  ")


def test_without_fl_only_the_users_folders_are_searched(disk):
    roots = browser_index.browser_roots(None, disk[1])

    assert [name for name, _ in roots] == ["Plugin presets", "Project bones"]


@pytest.fixture
def tools(disk, monkeypatch) -> dict[str, Any]:
    monkeypatch.setenv("FL_MCP_SETTINGS_DIR", str(disk[1] / "Settings"))
    monkeypatch.setattr(fl_windows, "fl_install_dir", lambda: disk[0])
    collector = ToolCollector()
    register_browser_tools(collector)
    return collector.tools


def test_the_tool_returns_paths_the_loading_tools_take(tools):
    assert tools["fl_browser_search"]("grv kick") == {
        "matches": ["Packs/Drums/Kicks/Grv Kick 27.wav"], "total": 1}


def test_the_tool_says_when_fls_own_folders_could_not_be_searched(tools, monkeypatch):
    def no_fl():
        raise OSError("FL Studio isn't running")

    monkeypatch.setattr(fl_windows, "fl_install_dir", no_fl)

    result = tools["fl_browser_search"]("kick")

    assert result["matches"] == ["Project bones/Hardcore Kick 040 F.wav"]
    assert "not searched" in result["warning"]


@pytest.mark.parametrize("kwargs, message", [
    ({"query": ""}, "at least one word"),
    ({"query": "kick", "limit": 0}, "limit must be"),
])
def test_the_tool_refuses_bad_arguments(tools, kwargs, message):
    assert message in tools["fl_browser_search"](**kwargs)["error"]


def test_morphine_presets_are_found(disk):
    assert _search(disk, "morphine scream") == (
        ["Plugin presets/Generators/Morphine/Leads/LED Scream MC.mrp"], 1)
