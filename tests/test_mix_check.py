"""Mix checks (audio/mix_doctor.py), gain staging, and watching the meters."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from fl_studio_mcp.audio.mix_doctor import diagnose, gain_plan
from fl_studio_mcp.tools import audio as audio_tools
from fl_studio_mcp.tools import mix_check
from fl_studio_mcp.utils import connection
from fl_studio_mcp.utils.peak_watch import PeakWatch, to_dbfs
from tests.fakes import ScriptedConnection, ToolCollector


def stem(name: str, lufs: float, index: int = 0, peak: float = -3.0, **bands: float) -> dict:
    shares = {"sub": 0.0, "bass": 0.0, "low_mid": 0.0, "mid": 0.0, "high_mid": 0.0, "high": 0.0}
    shares.update(bands)
    return {"index": index, "name": name, "silent": False, "loudness_lufs": lufs,
            "peak_dbfs": peak, "frequency_percent": shares}


def kinds(findings: list[dict]) -> list[str]:
    return [f["kind"] for f in findings]


# --- the rules -----------------------------------------------------------------


def test_a_clipping_mix_is_a_problem_and_comes_first():
    findings = diagnose({"clipped_samples": 12, "peak_dbfs": 0.0},
                        [stem("Pad", -20, mid=100), stem("Lead", -50, mid=100)])

    assert findings[0]["severity"] == "problem" and findings[0]["kind"] == "clipping"
    assert "buried" in kinds(findings)


def test_a_peak_near_full_scale_is_a_check():
    assert kinds(diagnose({"clipped_samples": 0, "peak_dbfs": -0.1})) == ["headroom"]
    assert diagnose({"clipped_samples": 0, "peak_dbfs": -1.0}) == []


def test_stems_over_full_scale_count_only_without_the_master_effects():
    hot = [stem("Lead", -8, peak=2.5, mid=100)]

    assert kinds(diagnose(stems=hot, master_effects=False)) == ["too_hot"]
    assert diagnose(stems=hot, master_effects=True) == []


def test_a_part_with_low_end_of_its_own_is_pointed_out():
    stems = [stem("Kick", -10, sub=50, bass=40, mid=10),
             stem("Pad", -10, bass=20, mid=80),  # 20 % below 250 Hz: not a bass part
             stem("Hat", -10, high=100)]

    low = [f for f in diagnose(stems=stems) if f["kind"] == "low_end"]

    assert len(low) == 1 and low[0]["parts"] == ["Pad", "Kick"]
    assert "high_pass" in low[0]["message"]


def test_many_parts_sharing_the_low_end():
    stems = [stem(n, -10, sub=50, bass=50) for n in ("Kick", "Bass", "Sub", "Tom")]

    crowded = [f for f in diagnose(stems=stems) if f["kind"] == "low_end"]

    assert crowded[0]["parts"] == ["Kick", "Bass", "Sub", "Tom"]


def test_two_loud_parts_on_the_same_band_may_mask_each_other():
    stems = [stem("Lead", -10, mid=70, high=30), stem("Vocal", -11, mid=80, high=20),
             stem("Hat", -10, high=100), stem("Quiet", -30, mid=100)]

    masking = [f for f in diagnose(stems=stems) if f["kind"] == "masking"]

    assert [f["parts"] for f in masking] == [["Lead", "Vocal"]]


def test_silent_and_failed_stems_are_left_out():
    stems = [{"index": 0, "name": "A", "silent": True}, {"index": 1, "name": "B", "error": "x"}]

    assert diagnose(stems=stems) == []


def test_routing_checks():
    rack = [{"index": 0, "name": "Kick", "target_fx_track": 0},
            {"index": 1, "name": "Fade", "target_fx_track": 0, "is_automation": True}]
    tracks = [{"index": 0, "name": "Master", "routes": []},
              {"index": 1, "name": "Kick 1", "routes": [0], "channels": []},
              {"index": 2, "name": "Kick 2", "routes": [0], "channels": []},
              {"index": 3, "name": "Snare", "routes": [5], "channels": ["Kick ghost"]},
              {"index": 4, "name": "Insert 4", "routes": [0], "channels": ["Lead"]}]

    findings = diagnose(tracks=tracks, rack=rack)

    assert [(f["kind"], f["parts"]) for f in findings] == [
        ("unrouted", ["Kick"]),  # the automation clip doesn't count
        ("grouping", ["Kick 1", "Kick 2"]),  # Snare already goes to a bus
    ]


# --- gain staging ----------------------------------------------------------------


def test_gain_plan_brings_each_tracks_loudest_part_to_the_target():
    rack = [{"index": 0, "target_fx_track": 1}, {"index": 1, "target_fx_track": 1},
            {"index": 2, "target_fx_track": 2}, {"index": 3, "target_fx_track": 0}]
    tracks = [{"index": 1, "name": "Drums", "volume_db": 0.0},
              {"index": 2, "name": "Pad", "volume_db": 3.0}]
    stems = [stem("Kick", -8, 0, peak=-1.0), stem("Clap", -12, 1, peak=-4.0),
             stem("Pad", -30, 2, peak=-20.0), stem("Master only", -10, 3, peak=-2.0),
             {"index": 1, "name": "x", "silent": True}]

    plan = gain_plan(stems, rack, tracks, -6.0)

    assert plan == [
        {"track": 1, "name": "Drums", "peak_dbfs": -1.0, "loudest_channel": "Kick",
         "change_db": -5.0, "volume_db": 0.0, "new_volume_db": -5.0},
        {"track": 2, "name": "Pad", "peak_dbfs": -20.0, "loudest_channel": "Pad",
         "change_db": 14.0, "volume_db": 3.0, "new_volume_db": 5.6,
         "note": plan[1]["note"]},
    ]
    assert "tops out" in plan[1]["note"]


# --- the peak watch --------------------------------------------------------------


class Meters:
    def __init__(self, readings: list[dict[int, float]]) -> None:
        self.readings = readings
        self.calls = 0

    def send(self, action: str, params: dict | None) -> dict:
        assert action == "mixer.getPeaks"
        reading = self.readings[min(self.calls, len(self.readings) - 1)]
        self.calls += 1
        return {"peaks": [{"track": t, "peak": reading.get(t, 0.0)} for t in params["tracks"]]}


def wait_for(condition, seconds: float = 2.0) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.005)


def test_the_watch_keeps_the_highest_peak_and_counts_overs():
    meters = Meters([{0: 0.5, 1: 0.1}, {0: 1.2, 1: 0.2}, {0: 0.9, 1: 0.1}, {0: 1.0}])
    watch = PeakWatch(meters.send)

    watch.start([0, 1], interval=0.02)
    wait_for(lambda: meters.calls >= 6)
    result = watch.stop()

    assert not result["running"] and result["polls"] >= 6
    master, insert = result["tracks"]
    assert master["peak_dbfs"] == to_dbfs(1.2) == 1.6
    assert master["overs"] >= 3 and master["first_over_seconds"] is not None
    assert insert == {"track": 1, "peak_dbfs": to_dbfs(0.2), "overs": 0,
                      "first_over_seconds": None}


def test_the_watch_reports_what_stopped_it():
    watch = PeakWatch(lambda action, params: {"error": "FL Studio did not respond."})

    watch.start([0], interval=0.02)
    wait_for(lambda: not watch.running)

    assert watch.status()["error"] == "FL Studio did not respond."


def test_only_one_watch_at_a_time_and_bad_arguments():
    watch = PeakWatch(Meters([{0: 0.1}]).send)
    assert watch.status() == {}
    watch.start([0], interval=0.02)
    try:
        with pytest.raises(ValueError, match="already"):
            watch.start([0])
    finally:
        watch.stop()
    for bad in ({"tracks": []}, {"tracks": [0], "interval": 0.001},
                {"tracks": [0], "max_seconds": 0}):
        with pytest.raises(ValueError):
            watch.start(**bad)


def test_the_watch_stops_by_itself():
    watch = PeakWatch(Meters([{0: 0.1}]).send)

    watch.start([0], interval=0.02, max_seconds=0.05)
    wait_for(lambda: not watch.running)

    assert watch.status()["seconds"] < 1


# --- the tools -------------------------------------------------------------------

RACK = [{"index": 0, "name": "Kick", "target_fx_track": 1},
        {"index": 1, "name": "Pad", "target_fx_track": 2}]
TRACKS = [{"index": 0, "name": "Master", "routes": [], "volume_db": 0.0},
          {"index": 1, "name": "Kick", "routes": [0], "volume_db": 0.0, "channels": ["Kick"]},
          {"index": 2, "name": "Pad", "routes": [0], "volume_db": -2.0, "channels": ["Pad"]}]


@pytest.fixture
def fl(monkeypatch, tmp_path) -> tuple[dict[str, Any], ScriptedConnection, list]:
    conn = ScriptedConnection({
        "mixer.getAllTracks": {"tracks": TRACKS},
        "channels.getAll": {"channels": RACK},
        "transport.getStatus": {"is_playing": True},
        "mixer.getPeaks": {"peaks": [{"track": 0, "peak": 0.5}, {"track": 1, "peak": 0.25}]},
        "mixer.setTrackVolumeDb": {"volume_db": -5.04},
    })
    monkeypatch.setattr(connection, "get_connection", lambda: conn)
    monkeypatch.setenv(audio_tools.RENDER_DIR_ENV, str(tmp_path))
    renders: list = []
    stems = {"stems": [stem("Kick", -8, 0, peak=1.0, sub=60, bass=30, mid=10),
                       stem("Pad", -12, 1, peak=-14.0, bass=30, mid=70)]}

    def render_stems(indexes, timeout, keep, master_effects=True):
        renders.append(("stems", indexes, master_effects))
        return stems

    def render_audio(path: Path, timeout: float) -> float:
        renders.append(("mix", path))
        path.write_bytes(b"")
        return 1.0

    monkeypatch.setattr(audio_tools, "render_stems", render_stems)
    monkeypatch.setattr(audio_tools, "render_audio", render_audio)
    monkeypatch.setattr(audio_tools, "analyze_file", lambda path, window, tempo=True: {
        "loudness_lufs": -7.0, "peak_dbfs": 0.0, "clipped_samples": 40})
    collector = ToolCollector()
    mix_check.register_mix_check_tools(collector)
    return collector.tools, conn, renders


def test_diagnose_mix_renders_the_mix_then_the_stems_without_master_effects(fl):
    tools, _, renders = fl

    result = tools["fl_diagnose_mix"]()

    assert [r[0] for r in renders] == ["mix", "stems"]
    assert renders[1][2] is False
    assert not renders[0][1].exists()  # the mix's WAV is deleted
    assert result["mix"] == {"loudness_lufs": -7.0, "peak_dbfs": 0.0, "clipped_samples": 40}
    assert kinds(result["findings"])[:2] == ["clipping", "too_hot"]
    assert "low_end" in kinds(result["findings"])


def test_gain_stage_proposes_then_applies(fl):
    tools, conn, _ = fl

    proposed = tools["fl_gain_stage"]()

    assert [(t["track"], t["change_db"]) for t in proposed["tracks"]] == [(1, -7.0), (2, 8.0)]
    assert proposed["applied"] is False
    assert not any(action == "mixer.setTrackVolumeDb" for action, _ in conn.sent)

    applied = tools["fl_gain_stage"](apply=True)

    assert applied["applied"] is True
    sets = [p for action, p in conn.sent if action == "mixer.setTrackVolumeDb"]
    assert sets == [{"track": 1, "db": -7.0}, {"track": 2, "db": 5.6}]
    assert applied["tracks"][0]["new_volume_db"] == -5.0  # what FL answered
    assert "error" in tools["fl_gain_stage"](target_peak_dbfs=3)


def test_track_level_needs_playback(fl):
    tools, conn, _ = fl

    result = tools["fl_get_track_level"]([0, 1], seconds=0.05)

    assert result["polls"] >= 1
    assert [(t["name"], t["peak_dbfs"]) for t in result["tracks"]] == [("Master", -6.0),
                                                                        ("Kick", -12.0)]
    conn.results["transport.getStatus"] = {"is_playing": False}
    assert "fl_play" in tools["fl_get_track_level"]()["error"]
    assert "error" in tools["fl_get_track_level"](seconds=60)


def test_watch_tools(fl, monkeypatch):
    tools, _, _ = fl
    monkeypatch.setattr(mix_check, "watch", PeakWatch(connection.call))

    assert "error" in tools["fl_watch_peaks_status"]()
    assert "error" in tools["fl_watch_peaks_stop"]()
    assert tools["fl_watch_peaks_start"](interval=0.02) == {"watching": [0, 1, 2]}
    wait_for(lambda: mix_check.watch.status()["polls"] >= 2)
    assert tools["fl_watch_peaks_status"]()["running"] is True
    stopped = tools["fl_watch_peaks_stop"]()

    assert stopped["running"] is False
    assert stopped["tracks"][0]["peak_dbfs"] == -6.0
