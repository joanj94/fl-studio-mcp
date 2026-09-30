"""WAV reading, audio measurements, and the render and analysis tools."""

from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from fl_studio_mcp.audio import analysis
from fl_studio_mcp.audio.wav import read_wav
from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools import register_audio_tools
from fl_studio_mcp.utils import connection, fl_render
from tests.fakes import ScriptedConnection, ToolCollector

RATE = 48000


def sine(hz: float, seconds: float = 3.0, level_db: float = 0.0, rate: int = RATE) -> np.ndarray:
    time = np.arange(int(seconds * rate)) / rate
    return 10 ** (level_db / 20) * np.sin(2 * np.pi * hz * time)


def stereo(left: np.ndarray, right: np.ndarray | None = None) -> np.ndarray:
    return np.column_stack([left, left if right is None else right])


def write_wav(path: Path, samples: np.ndarray, rate: int, format_tag: int, bits: int) -> Path:
    if format_tag == 3:
        raw = samples.astype(f"<f{bits // 8}").tobytes()
    elif bits == 24:
        ints = np.round(samples * (2 ** 23 - 1)).astype("<i4")
        raw = ints.astype("<i4").reshape(-1, 1).view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    elif bits == 8:
        raw = np.round(samples * 127 + 128).astype(np.uint8).tobytes()
    else:
        raw = np.round(samples * (2 ** (bits - 1) - 1)).astype(f"<i{bits // 8}").tobytes()
    channels = samples.shape[1]
    block = channels * bits // 8
    fmt = struct.pack("<HHIIHH", format_tag, channels, rate, rate * block, block, bits)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"  # an odd-sized chunk, padded
    body += b"data" + struct.pack("<I", len(raw)) + raw
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


# --- WAV reading -----------------------------------------------------------


@pytest.mark.parametrize(("format_tag", "bits", "tolerance"), [
    (1, 8, 2e-2), (1, 16, 1e-4), (1, 24, 1e-6), (1, 32, 1e-8), (3, 32, 1e-6), (3, 64, 1e-12),
])
def test_read_wav_understands_pcm_and_float_formats(tmp_path, format_tag, bits, tolerance):
    samples = stereo(sine(440, 0.01, -6), sine(880, 0.01, -12))
    path = write_wav(tmp_path / "a.wav", samples, RATE, format_tag, bits)

    read, rate = read_wav(path)

    assert rate == RATE
    assert read.shape == samples.shape
    assert np.max(np.abs(read - samples)) < tolerance


def test_read_wav_understands_the_extensible_header(tmp_path):
    samples = stereo(sine(440, 0.01, -6))
    raw = np.round(samples * 32767).astype("<i2").tobytes()
    guid = struct.pack("<H", 1) + bytes(14)  # sub-format: PCM
    fmt = struct.pack("<HHIIHH", 0xFFFE, 2, RATE, RATE * 4, 4, 16) + struct.pack("<HHI", 22, 16, 3)
    fmt += guid
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt
    body += b"data" + struct.pack("<I", len(raw)) + raw
    path = tmp_path / "ext.wav"
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)

    read, _ = read_wav(path)

    assert np.max(np.abs(read - samples)) < 1e-4


@pytest.mark.parametrize("content", [b"", b"not a wav file at all", b"RIFF\x04\x00\x00\x00WAVE"])
def test_read_wav_rejects_other_files(tmp_path, content):
    path = tmp_path / "bad.wav"
    path.write_bytes(content)

    with pytest.raises(ValueError):
        read_wav(path)


def test_read_wav_rejects_unknown_sample_formats(tmp_path):
    path = write_wav(tmp_path / "a.wav", stereo(sine(440, 0.01)), RATE, 1, 16)
    data = bytearray(path.read_bytes())
    data[20:22] = struct.pack("<H", 7)  # mu-law

    path.write_bytes(bytes(data))

    with pytest.raises(ValueError, match="Unsupported"):
        read_wav(path)


# --- measurements ----------------------------------------------------------


@pytest.mark.parametrize("rate", [44100, 48000])
@pytest.mark.parametrize("level_db", [0.0, -23.0])
def test_loudness_of_a_stereo_997hz_sine_matches_the_standard(rate, level_db):
    # BS.1770: a full-scale 997 Hz sine in one channel reads -3.01 LUFS; in both, 3 dB more.
    samples = stereo(sine(997, 3.0, level_db, rate))

    loudness = analysis.integrated_loudness(analysis.k_weighted(samples, rate), rate)

    assert loudness == pytest.approx(level_db, abs=0.1)


def test_k_weighting_cuts_lows_and_lifts_highs():
    gain = 10 * np.log10(analysis.k_weighting_gain(np.array([30.0, 997.0, 10000.0]), RATE))

    # 0.691 dB at 997 Hz is what the standard's -0.691 offset cancels out
    assert gain[0] < -5 and gain[1] == pytest.approx(0.691, abs=0.01) and 3.5 < gain[2] < 4.5


def test_quiet_passages_are_gated_out_of_the_loudness():
    loud = sine(997, 3.0, -20)
    quiet = sine(997, 3.0, -50)
    samples = stereo(np.concatenate([loud, quiet]))

    loudness = analysis.integrated_loudness(analysis.k_weighted(samples, RATE), RATE)

    assert loudness == pytest.approx(-20.0, abs=0.3)


def test_loudness_is_none_for_silence_or_very_short_audio():
    silence = np.zeros((RATE, 2))

    assert analysis.integrated_loudness(silence, RATE) is None
    assert analysis.integrated_loudness(np.ones((100, 2)), RATE) is None


def test_timeline_shows_a_level_change_and_stays_short():
    samples = stereo(np.concatenate([sine(997, 4.0, -30), sine(997, 4.0, -10)]))

    timeline = analysis.loudness_timeline(analysis.k_weighted(samples, RATE), RATE, 4.0)

    assert [point["start"] for point in timeline] == [0.0, 4.0]
    assert timeline[0]["lufs"] == pytest.approx(-30, abs=0.3)
    assert timeline[1]["lufs"] == pytest.approx(-10, abs=0.3)
    assert len(analysis.loudness_timeline(samples, RATE, 0.001)) <= analysis.MAX_TIMELINE_POINTS


def test_timeline_covers_short_audio_and_the_last_partial_window():
    weighted = analysis.k_weighted(stereo(sine(997, 6.0, -20)), RATE)

    assert [p["start"] for p in analysis.loudness_timeline(weighted, RATE, 4.0)] == [0.0, 4.0]
    short = analysis.loudness_timeline(weighted[: 2 * RATE], RATE, 4.0)
    assert len(short) == 1 and short[0]["lufs"] == pytest.approx(-20, abs=0.3)
    # a sliver at the end, shorter than a loudness block, is not reported
    assert len(analysis.loudness_timeline(weighted[: int(4.1 * RATE)], RATE, 4.0)) == 1


def test_band_balance_finds_where_the_energy_is():
    balance = analysis.band_balance(sine(100) + sine(3000, level_db=-20), RATE)

    assert balance["bass"]["percent"] == pytest.approx(99.0, abs=0.2)
    assert balance["high_mid"]["db"] == pytest.approx(-20.0, abs=0.2)
    assert balance["sub"]["percent"] == 0.0
    assert analysis.band_balance(np.zeros(RATE), RATE)["mid"]["percent"] == 0.0


def _chord(*hz: float) -> np.ndarray:
    return sum(sine(f, 2.0, -12) for f in hz)


A_MINOR_SCALE = (220.0, 246.94, 261.63, 293.66, 329.63, 349.23, 392.0)


def test_key_estimate_tells_minor_from_its_relative_major():
    tonic_heavy = _chord(*A_MINOR_SCALE) + 2 * _chord(220.0, 261.63, 329.63)  # A minor triad

    key = analysis.estimate_key(tonic_heavy, RATE)

    assert key["key"] == "A minor"
    assert key["alternative"] == "C major"
    assert key["confidence"] > key["alternative_confidence"]


def test_key_estimate_is_none_for_silence():
    assert analysis.estimate_key(np.zeros(RATE), RATE) is None


def test_key_estimate_is_none_when_every_pitch_class_is_equally_strong():
    frequencies = 440.0 * 2 ** (np.arange(12) / 12)  # one bin per pitch class

    assert analysis._estimate_key(np.ones(12), frequencies) is None


def test_stereo_image_of_mono_wide_and_inverted_audio():
    tone, other = sine(440, 1.0, -6), sine(557, 1.0, -6)

    assert analysis.stereo_image(stereo(tone))["correlation"] == 1.0
    assert analysis.stereo_image(stereo(tone, -tone))["correlation"] == -1.0
    assert abs(analysis.stereo_image(stereo(tone, other))["correlation"]) < 0.05
    assert analysis.stereo_image(stereo(tone, other))["side_to_mid_db"] == pytest.approx(0, abs=0.5)
    assert analysis.stereo_image(tone.reshape(-1, 1)) is None
    assert analysis.stereo_image(np.zeros((10, 2))) == {
        "correlation": None, "side_to_mid_db": None,
    }


def test_analyze_reports_the_whole_picture():
    tone = np.concatenate([np.zeros(RATE), sine(440, 2.0, -6), np.zeros(RATE // 2)])

    result = analysis.analyze(stereo(tone), RATE)

    assert result["duration_seconds"] == 3.5
    assert result["channels"] == 2 and result["sample_rate"] == RATE
    assert result["peak_dbfs"] == pytest.approx(-6.0, abs=0.01)
    assert result["crest_factor_db"] == pytest.approx(3.0 + 2.4, abs=0.2)  # sine + the silence
    assert result["clipped_samples"] == 0
    assert result["silence_seconds"] == {"start": 1.0, "end": 0.5}
    # a little under the tone's -6: blocks at its edges are part silence
    assert -8.0 < result["loudness_lufs"] < -6.0
    assert result["frequency_balance"]["low_mid"]["percent"] > 99
    assert result["key"]["key"].startswith("A")


def test_analyze_counts_clipping_and_handles_silence():
    clipped = analysis.analyze(stereo(np.clip(sine(440, 1.0, 6), -1, 1)), RATE)
    silent = analysis.analyze(np.zeros((RATE, 2)), RATE)

    assert clipped["clipped_samples"] > 1000
    assert silent["loudness_lufs"] is None and silent["key"] is None
    assert silent["silence_seconds"] == {"start": 1.0, "end": 1.0}
    assert silent["peak_dbfs"] == analysis.FLOOR_DB


def test_analyze_rejects_empty_audio():
    with pytest.raises(ValueError):
        analysis.analyze(np.zeros((0, 2)), RATE)


def test_analyze_refuses_audio_too_long_to_hold_in_memory(monkeypatch):
    monkeypatch.setattr(analysis, "MAX_ANALYSIS_SECONDS", 1)

    with pytest.raises(ValueError, match="at most"):
        analysis.analyze(np.zeros((2 * RATE, 2)), RATE)


# --- render keystrokes -----------------------------------------------------


class FakeExport:
    """FL's export flow: Ctrl+R opens the save dialog, Enter the render window, Enter renders."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.events: list[str] = []
        self.windows = {"#32770": 0, "TWAVRenderForm": 0, "menus": 0}
        self.opens_dialog = True
        self.opens_render_window = True
        self.finishes = True
        self.writes_file = True
        self.accepts_path = True  # False: Enter leaves the save dialog open
        self.held: list[str] = []

    def pressed(self, key: str):
        fake = self

        class Held:
            def __enter__(self) -> None:
                fake.held.append(key)

            def __exit__(self, *exc: object) -> None:
                fake.held.remove(key)

        return Held()

    def tap(self, key: str) -> None:
        self.events.append("+".join([*self.held, key]))
        if self.held == ["ctrl"] and key == "r":
            self.windows["#32770"] = int(self.opens_dialog)
        elif key == "esc":
            self.windows["#32770"] = self.windows["TWAVRenderForm"] = 0
        elif key == "enter" and self.windows["#32770"] and not self.accepts_path:
            self.events[-1] = "enter (refused)"
        elif key == "enter" and self.windows["#32770"]:
            self.windows["#32770"] = 0
            self.windows["TWAVRenderForm"] = int(self.opens_render_window)
        elif key == "enter" and self.windows["TWAVRenderForm"] and self.finishes:
            self.windows["TWAVRenderForm"] = 0
            if self.writes_file:
                self.target.write_bytes(b"RIFF")

    def type(self, text: str) -> None:
        self.events.append(f"type {text}")


@pytest.fixture
def export(monkeypatch, tmp_path) -> FakeExport:
    fake = FakeExport(tmp_path / "out.wav")
    keys = SimpleNamespace(ctrl="ctrl", enter="enter", esc="esc")
    trigger = SimpleNamespace(focus=lambda: True, has_focus=lambda: True)
    monkeypatch.setattr(fl_render.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_render, "_keyboard", lambda: (fake, keys))
    monkeypatch.setattr(fl_render, "get_trigger", lambda: trigger)
    monkeypatch.setattr(fl_render, "WINDOW_TIMEOUT", 0.0)
    monkeypatch.setattr(fl_render.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(fl_render.fl_windows, "window_count", lambda cls: fake.windows[cls])
    monkeypatch.setattr(fl_render.fl_windows, "popup_menu_count", lambda: fake.windows["menus"])
    return fake


def test_render_walks_the_export_dialogs_and_waits_for_the_file(export):
    seconds = fl_render.render_wav(export.target, timeout=0.0)

    assert export.events == ["ctrl+r", f"type {export.target}", "enter", "enter"]
    assert seconds >= 0
    assert export.target.exists()


@pytest.mark.parametrize(("window", "reason"), [
    ("menus", "a menu is open"), ("#32770", "a dialog is open"), ("TWAVRenderForm", "already open"),
])
def test_render_does_not_start_while_fl_is_busy(export, window, reason):
    export.windows[window] = 1

    with pytest.raises(fl_render.RenderError, match=reason):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.events == []


def test_render_refuses_to_overwrite_a_file(export):
    export.target.write_bytes(b"old")

    with pytest.raises(fl_render.RenderError, match="already exists"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.events == []


def test_render_needs_the_output_folder(export, tmp_path):
    with pytest.raises(fl_render.RenderError, match="does not exist"):
        fl_render.render_wav(tmp_path / "missing" / "out.wav", timeout=0.0)


@pytest.mark.parametrize(("setting", "reason", "events"), [
    ("opens_dialog", "export dialog did not open", 1),
    ("opens_render_window", "render window did not open", 3),
    ("finishes", "did not finish", 4),
    ("writes_file", "was not written", 4),
])
def test_render_reports_the_step_that_failed(export, setting, reason, events):
    setattr(export, setting, False)

    with pytest.raises(fl_render.RenderError, match=reason):
        fl_render.render_wav(export.target, timeout=0.0)
    assert len(export.events) == events


def test_render_closes_the_save_dialog_when_fl_refuses_the_path(export):
    export.accepts_path = False

    with pytest.raises(fl_render.RenderError, match="render window did not open"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.events[-1] == "esc"
    assert export.windows["#32770"] == 0


def test_render_stops_typing_when_fl_loses_focus(export, monkeypatch):
    trigger = SimpleNamespace(focus=lambda: True, has_focus=lambda: False)
    monkeypatch.setattr(fl_render, "get_trigger", lambda: trigger)

    with pytest.raises(fl_render.RenderError, match="lost focus"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.events == ["ctrl+r"]


def test_render_needs_fl_in_front_and_windows(export, monkeypatch):
    trigger = SimpleNamespace(focus=lambda: False, has_focus=lambda: False)
    monkeypatch.setattr(fl_render, "get_trigger", lambda: trigger)
    with pytest.raises(fl_render.RenderError, match="brought to the front"):
        fl_render.render_wav(export.target, timeout=0.0)

    monkeypatch.setattr(fl_render.platform, "system", lambda: "Darwin")
    with pytest.raises(fl_render.RenderError, match="only supported on Windows"):
        fl_render.render_wav(export.target, timeout=0.0)


def test_render_reports_a_keyboard_failure(export, monkeypatch):
    def broken():
        raise OSError("no display")

    monkeypatch.setattr(fl_render, "_keyboard", broken)

    with pytest.raises(fl_render.RenderError, match="no display"):
        fl_render.render_wav(export.target, timeout=0.0)


# --- tools -----------------------------------------------------------------


@pytest.fixture
def tools(monkeypatch, tmp_path) -> dict[str, Any]:
    conn = ScriptedConnection({"transport.getStatus": {"loop_mode": "pattern"}})
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(tmp_path / "renders"))
    collector = ToolCollector()
    register_audio_tools(collector)
    return collector.tools


def _fl_renders(monkeypatch, samples: np.ndarray) -> list[Path]:
    rendered: list[Path] = []

    def render(path: Path, timeout: float) -> float:
        write_wav(path, samples, RATE, 1, 16)
        rendered.append(path)
        return 1.234

    monkeypatch.setattr(audio_tools, "render_wav", render)
    return rendered


def test_render_tool_writes_to_the_render_folder_and_analyses(tools, monkeypatch, tmp_path):
    rendered = _fl_renders(monkeypatch, stereo(sine(440, 1.0, -6)))

    result = tools["fl_render"]()

    assert Path(result["path"]) == rendered[0]
    assert rendered[0].parent == tmp_path / "renders" and rendered[0].suffix == ".wav"
    assert result["mode"] == "pattern" and result["render_seconds"] == 1.2
    assert result["analysis"]["peak_dbfs"] == pytest.approx(-6.0, abs=0.01)


def test_render_tool_never_reuses_a_file_name(tools, monkeypatch):
    rendered = _fl_renders(monkeypatch, stereo(sine(440, 0.1)))
    monkeypatch.setattr(audio_tools.time, "strftime", lambda fmt: "20260930-120000")

    tools["fl_render"](analyze=False)
    result = tools["fl_render"](analyze=False)

    assert [path.name for path in rendered] == [
        "render-20260930-120000.wav", "render-20260930-120000-2.wav",
    ]
    assert "analysis" not in result


def test_render_tool_takes_a_path_but_only_wav(tools, monkeypatch, tmp_path):
    rendered = _fl_renders(monkeypatch, stereo(sine(440, 0.1)))

    assert "must end in .wav" in tools["fl_render"](path=str(tmp_path / "song.mp3"))["error"]
    assert rendered == []

    tools["fl_render"](path=str(tmp_path / "song.wav"), analyze=False)
    assert rendered == [tmp_path / "song.wav"]


def test_render_tool_reports_a_failed_render(tools, monkeypatch):
    def fails(path: Path, timeout: float) -> float:
        raise fl_render.RenderError("a dialog is open")

    monkeypatch.setattr(audio_tools, "render_wav", fails)

    assert tools["fl_render"]() == {"error": "a dialog is open"}


def test_analyze_tool_measures_a_file(tools, tmp_path):
    path = write_wav(tmp_path / "a.wav", stereo(sine(997, 3.0, -14)), RATE, 3, 32)

    result = tools["audio_analyze"](str(path), window_seconds=1.0)

    assert result["loudness_lufs"] == pytest.approx(-14.0, abs=0.1)
    assert len(result["loudness_over_time"]) == 3


@pytest.mark.parametrize(("name", "content", "kwargs", "message"), [
    ("missing.wav", None, {}, "Could not analyse"),
    ("bad.wav", b"nope", {}, "Not a WAV file"),
    ("ok.wav", b"nope", {"window_seconds": 0}, "window_seconds"),
])
def test_analyze_tool_reports_problems(tools, tmp_path, name, content, kwargs, message):
    path = tmp_path / name
    if content is not None:
        path.write_bytes(content)

    assert message in tools["audio_analyze"](str(path), **kwargs)["error"]


def test_analyze_tool_reports_running_out_of_memory(tools, tmp_path, monkeypatch):
    path = write_wav(tmp_path / "a.wav", stereo(sine(440, 0.1)), RATE, 1, 16)

    def too_big(samples, rate, window):
        raise MemoryError

    monkeypatch.setattr(analysis, "analyze", too_big)

    assert "not enough memory" in tools["audio_analyze"](str(path))["error"]


def test_analysis_without_numpy_explains_how_to_install_it(tools, monkeypatch, tmp_path):
    import builtins

    real_import = builtins.__import__

    def no_analysis(name: str, *args: Any, **kwargs: Any):
        if name.startswith("fl_studio_mcp.audio"):
            raise ImportError("No module named 'numpy'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_analysis)

    assert "uv sync --extra analysis" in tools["audio_analyze"](str(tmp_path / "a.wav"))["error"]
