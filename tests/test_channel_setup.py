"""The plugin database lookup behind fl_add_channel, and FL window focus per platform.

fl_add_channel itself is tested in test_browser.py.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fl_studio_mcp.tools import register_channel_setup_tools
from fl_studio_mcp.utils import fl_trigger, plugin_database
from tests.fakes import ToolCollector


@pytest.fixture
def generators(settings_dir: Path) -> Path:
    """A plugin database with three generators, next to the Settings folder."""
    root = settings_dir.parent / "Presets" / "Plugin database" / "Generators"
    for relative in ("Sampler/Sampler.fst", "Synth classic/3x Osc.fst", "New/Fruity Slicer 2.FST",
                     "Synth classic/3x Osc.nfo", "Synth classic/3x Osc.png"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    return root


# --- plugin database -------------------------------------------------------


def test_list_generators_reads_names_and_categories(generators):
    assert plugin_database.list_generators() == {
        "3x Osc": "Synth classic",
        "Fruity Slicer 2": "New",
        "Sampler": "Sampler",
    }


def test_list_generators_reports_a_missing_database(settings_dir):
    with pytest.raises(ValueError, match="plugin database was not found"):
        plugin_database.list_generators()


def test_list_generators_reports_an_empty_database(generators):
    for path in generators.glob("*/*"):
        path.unlink()

    with pytest.raises(ValueError, match="No generators"):
        plugin_database.list_generators()


@pytest.mark.parametrize("name", ["3x Osc", "3X OSC", "  3x osc "])
def test_find_generator_ignores_case_and_outer_spaces(generators, name):
    assert plugin_database.find_generator(name) == "3x Osc"


def test_find_generator_rejects_partial_names_with_suggestions(generators):
    with pytest.raises(ValueError, match="Did you mean: Fruity Slicer 2"):
        plugin_database.find_generator("Fruity Slicer")


def test_find_generator_without_a_close_match_points_at_the_list(generators):
    with pytest.raises(ValueError, match="fl_list_generators"):
        plugin_database.find_generator("zzqq")


# --- focus -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "expected"), [("Windows", "windows"), ("Darwin", "macos"), ("Linux", False)]
)
def test_trigger_focus_uses_the_platform_method(monkeypatch, system, expected):
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: system)
    trigger = fl_trigger.FLStudioTrigger()
    monkeypatch.setattr(trigger, "_focus_fl_studio_windows", lambda: "windows")
    monkeypatch.setattr(trigger, "_focus_fl_studio_macos", lambda: "macos")

    assert trigger.focus() == expected


def test_trigger_has_focus_is_only_checked_on_windows(monkeypatch):
    monkeypatch.setattr(fl_trigger.platform, "system", lambda: "Darwin")

    assert fl_trigger.FLStudioTrigger().has_focus() is True


# --- tools -----------------------------------------------------------------


@pytest.fixture
def tools(generators) -> dict[str, Any]:
    collector = ToolCollector()
    register_channel_setup_tools(collector)
    return collector.tools


def test_list_generators_tool(tools):
    assert tools["fl_list_generators"]() == {"generators": [
        {"name": "3x Osc", "category": "Synth classic"},
        {"name": "Fruity Slicer 2", "category": "New"},
        {"name": "Sampler", "category": "Sampler"},
    ]}


def test_list_generators_tool_reports_a_missing_database(tools, generators):
    for path in sorted(generators.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    generators.rmdir()

    assert "plugin database" in tools["fl_list_generators"]()["error"]
