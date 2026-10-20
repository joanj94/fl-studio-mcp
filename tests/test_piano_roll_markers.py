"""Piano roll markers (key/scale, time signature, ...) through FL's own script runner."""

from __future__ import annotations

import json
import sys
import types

import pytest

from fl_studio_mcp.tools import piano_roll_markers
from fl_studio_mcp.utils import gopher
from tests.fakes import ToolCollector


class FakeMarker:
    def __init__(self) -> None:
        self.time, self.name, self.mode = 0, "Marker", 0
        self.tsnum, self.tsden, self.scale_root, self.scale_helper = 0, 0, 0, ""


class FakeScore:
    PPQ = 96

    def __init__(self) -> None:
        self.markers: list[FakeMarker] = []

    @property
    def markerCount(self) -> int:  # noqa: N802 - FL API name
        return len(self.markers)

    def addMarker(self, marker):  # noqa: N802 - FL API name
        self.markers.append(marker)

    def getMarker(self, index):  # noqa: N802 - FL API name
        return self.markers[index]

    def clearMarkers(self, all=False):  # noqa: N802 - FL API name
        self.markers.clear()


@pytest.fixture
def score(monkeypatch: pytest.MonkeyPatch) -> FakeScore:
    fake = FakeScore()
    module = types.ModuleType("flpianoroll")
    module.Marker = FakeMarker
    module.score = fake
    monkeypatch.setitem(sys.modules, "flpianoroll", module)

    def run(code, returns=None, timeout=0):
        namespace: dict = {}
        exec(code, namespace)
        # FL evaluates `returns` in the script's namespace; here it is our own fixed text.
        return None if returns is None else json.loads(json.dumps(eval(returns, namespace)))

    monkeypatch.setattr(gopher, "page_url", lambda *a: "ws://fake")
    monkeypatch.setattr(gopher, "run_piano_roll_python", run)
    monkeypatch.setattr(piano_roll_markers, "_select_target", lambda channel, pattern: None)
    return fake


@pytest.fixture
def tools() -> dict:
    collector = ToolCollector()
    piano_roll_markers.register_piano_roll_marker_tools(collector)
    return collector.tools


def test_scale_helper_marks_the_scales_pitch_classes_from_c():
    assert piano_roll_markers.scale_helper("C", "major") == "1,0,1,0,1,1,0,1,0,1,0,1"
    assert piano_roll_markers.scale_helper("F", "minor") == "1,1,0,1,0,1,0,1,1,0,1,0"


def test_key_marker(tools, score):
    result = tools["fl_add_piano_roll_marker"]("key", time=4, root="F", scale="minor")

    assert result["markers"] == [{"time": 4.0, "kind": "key", "name": "F minor",
                                  "root": "F", "scale_helper": "1,1,0,1,0,1,0,1,1,0,1,0"}]
    marker = score.markers[0]
    assert (marker.mode, marker.time, marker.scale_root) == (12, 384, 5)


def test_time_signature_marker(tools, score):
    result = tools["fl_add_piano_roll_marker"]("time_signature", numerator=7, denominator=8)

    assert result["markers"][0]["kind"] == "time_signature"
    assert result["markers"][0]["signature"] == "7/8"
    assert (score.markers[0].mode, score.markers[0].tsnum, score.markers[0].tsden) == (8, 7, 8)


def test_plain_and_pattern_length_markers(tools, score):
    tools["fl_add_piano_roll_marker"]("plain", time=2, name="Fill")
    tools["fl_add_piano_roll_marker"]("pattern_length", time=16)

    kinds = [(m["kind"], m["time"]) for m in tools["fl_get_piano_roll_markers"]()["markers"]]
    assert kinds == [("plain", 2.0), ("pattern_length", 16.0)]


@pytest.mark.parametrize("kwargs", [
    {"kind": "key", "root": "F"},
    {"kind": "key", "root": "H", "scale": "minor"},
    {"kind": "key", "root": "F", "scale": "nonsense"},
    {"kind": "time_signature", "numerator": 7},
    {"kind": "time_signature", "numerator": 7, "denominator": 5},
    {"kind": "plain"},
    {"kind": "chorus"},
    {"kind": "plain", "name": "x", "time": -1},
])
def test_bad_markers_are_refused(tools, score, kwargs):
    assert "error" in tools["fl_add_piano_roll_marker"](**kwargs)
    assert score.markers == []


def test_clear_markers(tools, score):
    tools["fl_add_piano_roll_marker"]("plain", name="A")

    assert tools["fl_clear_piano_roll_markers"]() == {"cleared": 1}
    assert score.markers == []


def test_without_fls_runner_the_tools_say_how_to_get_it(tools, monkeypatch):
    monkeypatch.setattr(gopher, "page_url", lambda *a: None)

    assert "fl_launch" in tools["fl_get_piano_roll_markers"]()["error"]
