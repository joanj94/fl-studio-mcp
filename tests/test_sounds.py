"""Sound features, similarity search over browser folders, its cache, and the effect list."""

import numpy as np
import pytest

from fl_studio_mcp.audio import sound_features
from fl_studio_mcp.audio.wav import write_wav
from fl_studio_mcp.tools import sounds
from fl_studio_mcp.utils import browser_index
from tests.fakes import ToolCollector

RATE = 44100


def kick(seconds=0.5, freq=55.0):
    t = np.arange(int(seconds * RATE)) / RATE
    return np.sin(2 * np.pi * freq * t) * np.exp(-t * 8)


def hat(seconds=0.1, seed=0):
    t = np.arange(int(seconds * RATE)) / RATE
    noise = np.random.default_rng(seed).standard_normal(len(t))
    return np.diff(noise, prepend=0) * np.exp(-t * 60) * 0.3


def test_a_kick_is_low_and_tonal_a_hat_high_and_noisy():
    low = sound_features.describe(sound_features.features(kick(), RATE))
    high = sound_features.describe(sound_features.features(hat(), RATE))
    assert low["bands"]["sub"] > 0.8 and high["bands"]["high"] > 0.4
    assert low["brightness_hz"] < 200 < 3000 < high["brightness_hz"]
    assert high["noisiness"] > low["noisiness"]
    assert high["decay_ms"] < low["decay_ms"]


def test_silence_has_no_features():
    assert sound_features.features(np.zeros(1000), RATE) is None


def test_nearest_ranks_the_same_kind_first():
    candidates = {"kick b": sound_features.features(kick(0.6, 60), RATE),
                  "hat a": sound_features.features(hat(seed=1), RATE),
                  "hat b": sound_features.features(hat(0.15, seed=2), RATE)}
    ranked = sound_features.nearest(sound_features.features(kick(), RATE), candidates, 3)
    assert ranked[0][0] == "kick b"


@pytest.fixture
def pack(settings_dir, monkeypatch):
    bones = settings_dir.parent / "Projects" / "Project bones" / "Pack"
    bones.mkdir(parents=True)
    write_wav(bones / "Boom 1.wav", kick(), RATE)
    write_wav(bones / "Boom 2.wav", kick(0.7, 50), RATE)
    write_wav(bones / "Tss 1.wav", hat(seed=3), RATE)
    write_wav(bones / "Tss 2.wav", hat(0.08, seed=4), RATE)
    (bones / "Broken.wav").write_bytes(b"not audio")
    monkeypatch.setattr(sounds, "_roots", lambda: browser_index.browser_roots(
        None, settings_dir.parent))
    monkeypatch.setattr(sounds, "measure", _measure_without_ffmpeg)
    return bones


def _measure_without_ffmpeg(path):
    from fl_studio_mcp.audio.wav import read_wav

    samples, rate = read_wav(path)
    return sound_features.features(samples, rate)


def test_similar_finds_the_other_kick_and_skips_unreadable_files(pack):
    result = sounds.find_similar("Project bones/Pack/Boom 1.wav", "Project bones", 3)
    assert result["matches"][0]["path"] == "Project bones/Pack/Boom 2.wav"
    assert result["compared"] == 3  # the reference itself isn't a match
    assert result["unreadable"] == 1


def test_measured_files_are_kept_and_reused(pack, monkeypatch):
    sounds.find_similar("Project bones/Pack/Tss 1.wav", "Project bones", 2)
    measured = []

    def count(path):
        measured.append(path)
        return _measure_without_ffmpeg(path)

    monkeypatch.setattr(sounds, "measure", count)
    result = sounds.find_similar("Project bones/Pack/Tss 1.wav", "Project bones", 2)
    assert measured == [pack / "Tss 1.wav"]  # only the reference again
    assert result["matches"][0]["path"] == "Project bones/Pack/Tss 2.wav"


def test_files_left_when_time_runs_out_are_reported(pack, monkeypatch):
    clock = iter(range(0, 1000, 10))
    monkeypatch.setattr(sounds.time, "monotonic", lambda: next(clock))
    result = sounds.find_similar("Project bones/Pack/Boom 1.wav", "Project bones", 3,
                                 max_seconds=5)
    assert "call again" in result["warning"]


def test_a_reference_nowhere_to_be_found_is_an_error(pack):
    tools = ToolCollector()
    sounds.register_sound_tools(tools)
    result = tools.tools["fl_browser_similar"]("Project bones/Pack/None.wav")
    assert "neither a file" in result["error"]


def test_effects_are_listed_by_category(settings_dir):
    effects = settings_dir.parent / "Presets" / "Plugin database" / "Effects"
    for category, name in (("Dynamics", "Fruity Limiter"), ("Filter", "Fruity Parametric EQ 2")):
        (effects / category).mkdir(parents=True, exist_ok=True)
        (effects / category / f"{name}.fst").write_bytes(b"")
    tools = ToolCollector()
    sounds.register_sound_tools(tools)
    result = tools.tools["fl_list_effects"]("dynamics")
    assert result["effects"] == [{"name": "Fruity Limiter", "category": "Dynamics",
                                  "path": "Plugin database/Effects/Dynamics/Fruity Limiter"}]
    assert result["categories"] == ["Dynamics", "Filter"]
    assert "there are" in tools.tools["fl_list_effects"]("reverb")["error"]
