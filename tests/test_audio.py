"""WAV reading, audio measurements, and the render and analysis tools."""

from __future__ import annotations

import struct
from pathlib import Path
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


# --- render through FL's export dialog -------------------------------------

UP, RIGHT, ENTER, HOME, END = 0x26, 0x27, 0x0D, 0x24, 0x23
DIALOG, RENDER_WINDOW = "#32770", "TWAVRenderForm"


class FakeExport:
    """FL's export flow: File > Export > Wave file, the Save dialog, the render window."""

    def __init__(self, target: Path) -> None:
        self.target = target
        self.events: list = []
        self.windows = {DIALOG: 0, RENDER_WINDOW: 0}
        self.menus = 0
        self.minimized = False
        self.opens_file_menu = True
        self.has_export_submenu = True
        self.opens_dialog = True
        self.opens_render_window = True
        self.finishes = True
        self.writes_file = True
        self.pointer_on_menu = False
        self.message: str | None = None
        self.submenu_top = 259  # seen live: the File menu spans 36-334, Export opens at 259

    def open_message(self) -> str | None:
        return self.message

    def popup_menu_rects(self) -> list:
        menus = [(4, 36, 216, 334), (214, self.submenu_top, 393, self.submenu_top + 358)]
        return menus[:self.menus]

    def pointer_over_menu(self) -> bool:
        return self.pointer_on_menu

    def restore_if_minimized(self) -> bool:
        was_minimized, self.minimized = self.minimized, False
        return was_minimized

    def click_main_menu(self) -> bool:
        self.events.append("file menu")
        self.menus = int(self.opens_file_menu and not self.minimized)
        return True

    def press_in_popup_menu(self, key: int, menus: int = 1) -> bool:
        if self.menus != menus:
            return False
        self.events.append(key)
        if key == RIGHT and self.has_export_submenu:
            self.menus = 2
        elif key == ENTER:
            self.menus = 0
            self.windows[DIALOG] = int(self.opens_dialog)
        return True

    def confirm_dialog_with_file_name(self, name: str) -> bool:
        self.events.append(f"save as {name}")
        if self.opens_render_window:
            self.windows[DIALOG] = 0
            self.windows[RENDER_WINDOW] = 1
        return True

    def press_in_window(self, class_name: str, key: int) -> bool:
        self.events.append(f"{class_name} {key}")
        if self.finishes:
            self.windows[RENDER_WINDOW] = 0
            if self.writes_file:
                self.target.write_bytes(b"RIFF")
        return True

    def close_popup_menus(self) -> None:
        self.menus = 0

    def cancel_dialogs(self) -> None:
        self.windows[DIALOG] = 0

    def close_windows(self, class_name: str) -> None:
        self.events.append(f"close {class_name}")
        self.windows[class_name] = 0


@pytest.fixture
def export(monkeypatch, tmp_path) -> FakeExport:
    fake = FakeExport(tmp_path / "out.wav")
    windows = fl_render.fl_windows
    monkeypatch.setattr(fl_render.platform, "system", lambda: "Windows")
    monkeypatch.setattr(fl_render, "WINDOW_TIMEOUT", 0.0)
    monkeypatch.setattr(fl_render.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(windows, "window_count", lambda cls: fake.windows[cls])
    monkeypatch.setattr(windows, "popup_menu_count", lambda: fake.menus)
    for name in ("restore_if_minimized", "click_main_menu", "pointer_over_menu",
                 "open_message", "popup_menu_rects",
                 "press_in_popup_menu", "confirm_dialog_with_file_name", "press_in_window",
                 "close_popup_menus", "cancel_dialogs", "close_windows"):
        monkeypatch.setattr(windows, name, getattr(fake, name))
    return fake


def test_render_walks_the_export_dialogs_and_waits_for_the_file(export):
    seconds = fl_render.render_wav(export.target, timeout=0.0)

    # End is Exit; above it Revert, then Export. Right opens its submenu, whose first
    # entry (Home) is the wave file.
    assert export.events == ["file menu", END, UP, UP, RIGHT, HOME, ENTER,
                             f"save as {export.target}", f"{RENDER_WINDOW} {ENTER}"]
    assert seconds >= 0
    assert export.target.exists()


def test_a_minimized_fl_is_brought_back_before_rendering(export):
    export.minimized = True

    fl_render.render_wav(export.target, timeout=0.0)

    assert export.target.exists()


@pytest.mark.parametrize(("window", "reason"), [
    ("menus", "a menu is open"), (DIALOG, "a dialog is open"), (RENDER_WINDOW, "already open"),
])
def test_render_does_not_start_while_fl_is_busy(export, window, reason):
    if window == "menus":
        export.menus = 1
    else:
        export.windows[window] = 1

    with pytest.raises(fl_render.RenderError, match=reason):
        fl_render.render_wav(export.target, timeout=0.0)
    assert "file menu" not in export.events


def test_render_refuses_to_overwrite_a_file(export):
    export.target.write_bytes(b"old")

    with pytest.raises(fl_render.RenderError, match="already exists"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.events == []


def test_render_needs_the_output_folder(export, tmp_path):
    with pytest.raises(fl_render.RenderError, match="does not exist"):
        fl_render.render_wav(tmp_path / "missing" / "out.wav", timeout=0.0)


@pytest.mark.parametrize(("setting", "reason"), [
    ("opens_file_menu", "File menu did not open"),
    ("has_export_submenu", "Export submenu was not where it was expected"),
    ("opens_dialog", "export dialog did not open"),
    ("opens_render_window", "render window did not open"),
])
def test_render_reports_the_step_that_failed_and_leaves_nothing_open(export, setting, reason):
    setattr(export, setting, False)

    with pytest.raises(fl_render.RenderError, match=reason):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.menus == 0 and export.windows == {DIALOG: 0, RENDER_WINDOW: 0}
    if setting in ("opens_file_menu", "has_export_submenu"):
        assert ENTER not in export.events


def test_render_chooses_nothing_in_a_submenu_that_is_not_exports(export):
    export.submenu_top = 55  # seen live: where the submenus that open other projects are

    with pytest.raises(fl_render.RenderError, match="not where the File menu's Export"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert ENTER not in export.events and export.menus == 0


def test_render_does_not_start_while_fl_shows_a_message_box(export):
    export.message = "FL Studio trial"

    with pytest.raises(fl_render.RenderError, match="'FL Studio trial'.*fl_close_message"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert "file menu" not in export.events


def test_render_chooses_nothing_while_the_pointer_is_over_the_menu(export):
    export.pointer_on_menu = True

    with pytest.raises(fl_render.RenderError, match="pointer is over"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert ENTER not in export.events and export.menus == 0


def test_a_render_that_is_only_slow_is_left_running(export):
    export.finishes = False

    with pytest.raises(fl_render.RenderError, match="did not finish"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.windows[RENDER_WINDOW] == 1
    assert f"close {RENDER_WINDOW}" not in export.events


def test_render_reports_a_file_that_was_not_written(export):
    export.writes_file = False

    with pytest.raises(fl_render.RenderError, match="was not written"):
        fl_render.render_wav(export.target, timeout=0.0)


def test_render_is_windows_only(export, monkeypatch):
    monkeypatch.setattr(fl_render.platform, "system", lambda: "Darwin")

    with pytest.raises(fl_render.RenderError, match="only supported on Windows"):
        fl_render.render_wav(export.target, timeout=0.0)


def test_render_reports_a_failure_to_reach_fls_windows(export, monkeypatch):
    def broken() -> bool:
        raise OSError("no desktop")

    monkeypatch.setattr(fl_render.fl_windows, "click_main_menu", broken)

    with pytest.raises(fl_render.RenderError, match="no desktop"):
        fl_render.render_wav(export.target, timeout=0.0)


def test_a_failure_halfway_the_export_leaves_nothing_open(export, monkeypatch):
    def broken(name: str) -> bool:
        raise OSError("no desktop")

    monkeypatch.setattr(fl_render.fl_windows, "confirm_dialog_with_file_name", broken)

    with pytest.raises(fl_render.RenderError, match="no desktop"):
        fl_render.render_wav(export.target, timeout=0.0)
    assert export.menus == 0 and export.windows == {DIALOG: 0, RENDER_WINDOW: 0}


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


class Rack:
    """A connection with three channels (the second one muted) whose solo state is tracked."""

    def __init__(self) -> None:
        self.names = ["Kick", "Pad", "Lead"]
        self.muted = {1}
        self.soloed: int | None = None
        self.sent: list[tuple[str, dict]] = []
        self.fail: str | None = None

    def send_command(self, action: str, params: dict | None = None, timeout: float = 2.0):
        params = params or {}
        self.sent.append((action, params))
        if action == self.fail:
            raise RuntimeError("MIDI port closed")
        result: dict = {}
        if action == "channels.getAll":
            result = {"channels": [
                {"index": i, "name": name, "is_muted": i in self.muted}
                for i, name in enumerate(self.names)
            ]}
        elif action == "transport.getStatus":
            result = {"loop_mode": "song"}
        elif action == "channels.solo":
            self.soloed = params["index"] if params["solo"] else None
            self.muted = set()  # FL: taking a solo off unmutes everything
        elif action == "channels.mute":
            self.muted.add(params["index"])
        return {"success": True, "id": "x", **result}


@pytest.fixture
def rack(tools, monkeypatch) -> Rack:
    fake = Rack()
    monkeypatch.setattr(connection, "get_connection", lambda: fake)
    return fake


def _each_stem_sounds(monkeypatch, rack: Rack, levels: dict[int, float | None]) -> list[Path]:
    """FL renders the soloed channel: a tone at its level, or silence for None."""
    rendered: list[Path] = []

    def render(path: Path, timeout: float) -> float:
        level = levels[rack.soloed]
        tone = sine(440, 1.0, level) if level is not None else np.zeros(RATE)
        write_wav(path, stereo(tone), RATE, 1, 16)
        rendered.append(path)
        return 0.5

    monkeypatch.setattr(audio_tools, "render_wav", render)
    return rendered


def test_stems_render_every_unmuted_channel_on_its_own(tools, rack, monkeypatch):
    rendered = _each_stem_sounds(monkeypatch, rack, {0: -6, 2: None})

    result = tools["fl_render_stems"]()

    assert result["mode"] == "song"
    kick, lead = result["stems"]
    assert (kick["index"], kick["name"], kick["silent"]) == (0, "Kick", False)
    assert kick["peak_dbfs"] == pytest.approx(-6.0, abs=0.01)
    assert kick["frequency_percent"]["low_mid"] > 99
    assert set(kick["key"]) >= {"key", "confidence"}
    assert lead == {"index": 2, "name": "Lead", "silent": True}
    assert not any(path.exists() for path in rendered)  # the stem files are not kept


def test_stems_leave_mutes_and_solos_as_they_were(tools, rack, monkeypatch):
    _each_stem_sounds(monkeypatch, rack, {0: -6, 2: -6})

    assert "warning" not in tools["fl_render_stems"]()
    assert rack.soloed is None and rack.muted == {1}


def test_stems_can_be_limited_to_roles_and_kept(tools, rack, monkeypatch):
    rendered = _each_stem_sounds(monkeypatch, rack, {1: -12})

    result = tools["fl_render_stems"](channels=["pad"], keep_files=True)

    assert [stem["name"] for stem in result["stems"]] == ["Pad"]  # muted, but asked for
    assert Path(result["stems"][0]["path"]) == rendered[0] and rendered[0].exists()
    assert rendered[0].name.startswith("stem-1-")


def test_stems_reject_an_unknown_role_before_touching_fl(tools, rack):
    assert "tuba" in tools["fl_render_stems"](channels=["tuba"])["error"]
    assert "channels.solo" not in [action for action, _ in rack.sent]


def test_stems_reject_a_channel_the_rack_does_not_have(tools, rack):
    assert "no channel 99" in tools["fl_render_stems"](channels=[0, 99])["error"]
    assert "channels.solo" not in [action for action, _ in rack.sent]


def test_a_mono_stem_has_no_stereo_correlation(tools, rack, monkeypatch):
    def render(path: Path, timeout: float) -> float:
        write_wav(path, sine(440, 1.0, -6).reshape(-1, 1), RATE, 1, 16)
        return 0.5

    monkeypatch.setattr(audio_tools, "render_wav", render)

    stem = tools["fl_render_stems"](channels=[0])["stems"][0]

    assert stem["silent"] is False and stem["stereo_correlation"] is None


def test_a_failed_stem_render_takes_the_solo_off_again(tools, rack, monkeypatch):
    def fails(path: Path, timeout: float) -> float:
        raise fl_render.RenderError("a dialog is open")

    monkeypatch.setattr(audio_tools, "render_wav", fails)

    assert tools["fl_render_stems"]() == {"error": "a dialog is open"}
    assert rack.soloed is None and rack.muted == {1}


def test_stems_report_a_lost_connection(tools, rack):
    rack.fail = "channels.getAll"

    assert tools["fl_render_stems"]() == {"error": "MIDI port closed"}


def test_the_measurements_are_plain_json(tools, tmp_path):
    import json

    path = write_wav(tmp_path / "a.wav", stereo(sine(440, 1.0, -6)), RATE, 1, 16)

    result = tools["audio_analyze"](str(path))

    assert json.loads(json.dumps(result)) == result  # no numpy numbers left in it
    assert type(result["peak_dbfs"]) is float


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
