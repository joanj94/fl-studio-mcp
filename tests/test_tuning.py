"""Tests for fl_measure_pitch: one note played alone, rendered and compared."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools import register_tuning_tools, tuning
from fl_studio_mcp.utils import connection
from tests.fakes import ToolCollector
from tests.test_audio import RATE, stereo, write_wav


def hz_of(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


class Project:
    """A connection to a project with two channels, patterns 1-2 used and pattern 2 active."""

    def __init__(self) -> None:
        self.pattern = 2
        self.mode = "pattern"
        self.soloed: int | None = None
        self.rolls: dict[tuple[int, int], list[dict]] = {}
        self.fail: str | None = None
        self.sent: list[str] = []

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        params = params or {}
        self.sent.append(action)
        result: dict = {}
        if action == self.fail:
            result = {"error": "FL said no"}
        elif action == "channels.getAll":
            result = {"channels": [{"index": 0, "name": "Kick", "is_muted": False},
                                   {"index": 1, "name": "Lead", "is_muted": False}]}
        elif action == "transport.getStatus":
            result = {"loop_mode": self.mode}
        elif action == "transport.setLoopMode":
            self.mode = params["mode"]
        elif action == "patterns.getAll":
            result = {"current": self.pattern}
        elif action == "patterns.findEmpty":
            result = {"index": 3}
        elif action == "patterns.select":
            self.pattern = params["index"]
        elif action == "channels.solo":
            self.soloed = params["index"] if params["solo"] else None
        return {"success": "error" not in result, "id": "x", **result}


@pytest.fixture
def project(monkeypatch, tmp_path) -> Project:
    fake = Project()
    monkeypatch.setattr(connection, "get_connection", lambda: fake)
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(tmp_path))

    def write_notes(notes: list[dict], channel: int, pattern: int) -> None:
        fake.pattern = pattern  # writing to a roll selects its pattern
        fake.rolls[(channel, pattern)] = notes

    monkeypatch.setattr(tuning, "write_notes", write_notes)
    return fake


@pytest.fixture
def tools(project) -> dict[str, Any]:
    collector = ToolCollector()
    register_tuning_tools(collector)
    return collector.tools


def _channel_sounds(monkeypatch, project: Project, sound) -> list[Path]:
    """FL renders `sound(midi)` for the note in the soloed channel's roll."""
    rendered: list[Path] = []

    def render(path: Path, timeout: float) -> float:
        assert project.mode == "pattern"
        notes = project.rolls[(project.soloed, project.pattern)]
        write_wav(path, stereo(sound(notes[0]["midi"])), RATE, 1, 16)
        rendered.append(path)
        return 0.5

    monkeypatch.setattr(audio_tools, "render_wav", render)
    return rendered


def _tone(midi: float, seconds: float = 1.0) -> np.ndarray:
    return 0.5 * np.sin(2 * np.pi * hz_of(midi) * np.arange(int(seconds * RATE)) / RATE)


def test_a_channel_that_plays_what_it_is_given_is_in_tune(tools, project, monkeypatch):
    rendered = _channel_sounds(monkeypatch, project, _tone)

    result = tools["fl_measure_pitch"]("lead", note="A3")

    assert result["sent"] == "A3" and result["heard"]["note"] == "A3"
    assert (result["offset_semitones"], result["transpose_to_correct"]) == (0, 0)
    assert result["summary"].startswith("In tune")
    assert "warning" not in result
    assert not rendered[0].exists()


def test_a_sample_with_its_own_pitch_tells_how_to_write_its_notes(tools, project, monkeypatch):
    # A sampler: MIDI 60 plays the sample as recorded, here at F1 (29).
    _channel_sounds(monkeypatch, project, lambda midi: _tone(midi - 60 + 29))

    result = tools["fl_measure_pitch"]("kick", key_root="G")

    assert result["sent"] == "C4" and result["heard"]["note"] == "F1"
    assert result["offset_semitones"] == -31
    assert result["transpose_to_correct"] == -5  # F is 5 above C: write 5 lower
    assert result["note_for_root"] == "D4"  # two semitones up turns the F into a G
    assert "-5 semitones" in result["summary"]


def test_a_detuned_sound_gets_a_fine_pitch_to_add(tools, project, monkeypatch):
    _channel_sounds(monkeypatch, project, lambda midi: _tone(midi + 0.3))

    result = tools["fl_measure_pitch"](1, note=60)

    assert result["transpose_to_correct"] == 0
    assert result["heard"]["cents"] == pytest.approx(30, abs=3)
    assert "fine_pitch -3" in result["summary"]


def test_an_octave_off_still_counts_as_in_tune(tools, project, monkeypatch):
    _channel_sounds(monkeypatch, project, lambda midi: _tone(midi - 12))

    result = tools["fl_measure_pitch"](1, note=60)

    assert result["offset_semitones"] == -12 and result["transpose_to_correct"] == 0
    assert "1 octave(s) down" in result["summary"]


def test_noise_has_no_pitch_and_needs_no_tuning(tools, project, monkeypatch):
    noise = np.random.default_rng(3).uniform(-0.5, 0.5, RATE)
    _channel_sounds(monkeypatch, project, lambda midi: noise)

    result = tools["fl_measure_pitch"](0)

    assert result["heard"] is None
    assert "No clear pitch" in result["summary"]
    assert "transpose_to_correct" not in result


def test_a_sound_that_wanders_is_reported_as_unsteady(tools, project, monkeypatch):
    wander = np.concatenate([_tone(60 + step, 0.3) for step in range(5)])
    _channel_sounds(monkeypatch, project, lambda midi: wander)

    result = tools["fl_measure_pitch"](1)

    assert "No steady pitch" in result["summary"]
    assert "transpose_to_correct" not in result


def test_a_sound_in_several_octaves_is_steady(tools, project, monkeypatch):
    # Oscillators an octave apart, each loudest for a while: one pitch class throughout.
    octaves = np.concatenate([_tone(60, 0.5), _tone(72, 0.4), _tone(48, 0.4)])
    _channel_sounds(monkeypatch, project, lambda midi: octaves)

    result = tools["fl_measure_pitch"](1)

    assert result["summary"].startswith("In tune")


def test_a_pitch_that_glides_into_its_note_is_explained(tools, project, monkeypatch):
    glide = np.concatenate([_tone(60 + step, 0.25) for step in (5, 4, 3, 2, 1)] + [_tone(60, 0.5)])
    _channel_sounds(monkeypatch, project, lambda midi: glide)

    result = tools["fl_measure_pitch"](1)

    assert result["heard"]["ends_on"] == "C4"
    assert "The pitch glides" in result["summary"]
    assert "transpose_to_correct" not in result


def test_the_project_is_left_as_it_was(tools, project, monkeypatch):
    project.mode = "song"
    _channel_sounds(monkeypatch, project, _tone)

    tools["fl_measure_pitch"](1)

    assert (project.pattern, project.mode, project.soloed) == (2, "song", None)
    assert project.rolls[(1, 3)] == []


def test_a_failed_render_still_cleans_up(tools, project, monkeypatch):
    def render(path: Path, timeout: float) -> float:
        raise audio_tools.RenderError("FL Studio is showing a dialog")

    monkeypatch.setattr(audio_tools, "render_wav", render)

    result = tools["fl_measure_pitch"](1)

    assert result == {"error": "FL Studio is showing a dialog"}
    assert (project.pattern, project.soloed, project.rolls[(1, 3)]) == (2, None, [])


def test_a_note_that_cannot_be_written_is_an_error(tools, project, monkeypatch):
    def write_notes(notes, channel, pattern):
        if notes:
            raise ValueError("The piano roll script did not answer")

    monkeypatch.setattr(tuning, "write_notes", write_notes)

    assert tools["fl_measure_pitch"](1) == {"error": "The piano roll script did not answer"}
    assert "channels.solo" not in project.sent


def test_what_could_not_be_put_back_is_reported(tools, project, monkeypatch):
    _channel_sounds(monkeypatch, project, _tone)
    project.fail = "patterns.select"

    result = tools["fl_measure_pitch"](1)

    assert result["heard"]["note"] == "C4"
    assert "pattern 3 is still selected" in result["warning"]


@pytest.mark.parametrize("kwargs, message", [
    ({"channel": "tuba"}, "tuba"),
    ({"channel": 1, "note": "H9"}, "Invalid pitch"),
    ({"channel": 1, "key_root": "X"}, "Invalid note name"),
    ({"channel": 1, "beats": 0}, "beats must be"),
])
def test_bad_arguments_are_refused_before_fl_is_touched(tools, project, kwargs, message):
    result = tools["fl_measure_pitch"](**kwargs)

    assert message in result["error"]
    assert not any(action.startswith(("patterns.", "transport.")) for action in project.sent)


def test_a_lost_connection_while_writing_the_note_is_an_error(tools, project, monkeypatch):
    def write_notes(notes, channel, pattern):
        raise RuntimeError("MIDI port closed")

    monkeypatch.setattr(tuning, "write_notes", write_notes)

    assert tools["fl_measure_pitch"](1) == {"error": "MIDI port closed"}
    assert project.pattern == 2  # the clean-up after it still ran
