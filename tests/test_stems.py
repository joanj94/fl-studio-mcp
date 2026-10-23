"""Stem separation: device choice, argument checks and the files the tool writes.

Nothing here needs torch, Demucs or a GPU: torch is a fake and the separation is
replaced where the tool is tested.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from fl_studio_mcp.audio import stems
from fl_studio_mcp.audio.wav import read_wav, write_wav
from fl_studio_mcp.tools.stems import register_stem_tools
from tests.fakes import ToolCollector


def fake_torch(cuda=True, cuda_build=True, kernels=True, mps=False):
    def ones(*args, device=None, **kwargs):
        if device == "cuda" and not kernels:
            raise RuntimeError("no kernel image is available for execution on the device")
        return SimpleNamespace(sum=lambda: SimpleNamespace(item=lambda: 1.0))

    return SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: cuda,
                             get_device_name=lambda i: "Test Card"),
        version=SimpleNamespace(cuda="12.6" if cuda_build else None),
        backends=SimpleNamespace(mps=SimpleNamespace(is_available=lambda: mps)),
        ones=ones,
        nn=SimpleNamespace(functional=SimpleNamespace(conv1d=lambda x, w: x)),
    )


def test_auto_takes_a_working_gpu():
    assert stems.choose_device("auto", fake_torch()) == ("cuda", "GPU: Test Card")


def test_auto_falls_back_to_the_cpu_on_a_cpu_only_torch():
    device, note = stems.choose_device("auto", fake_torch(cuda=False, cuda_build=False))
    assert device == "cpu"
    assert "stems-gpu" in note


def test_a_card_this_torch_has_no_kernels_for_counts_as_no_gpu():
    device, note = stems.choose_device("auto", fake_torch(kernels=False))
    assert device == "cpu"
    assert "no kernel image" in note


def test_auto_takes_apples_gpu_when_there_is_no_cuda():
    assert stems.choose_device("auto", fake_torch(cuda=False, mps=True))[0] == "mps"


def test_cuda_asked_for_by_name_that_does_not_work_is_an_error():
    with pytest.raises(ValueError, match="CUDA can't be used"):
        stems.choose_device("cuda", fake_torch(cuda=False))


def test_cpu_asked_for_is_used_even_with_a_gpu():
    assert stems.choose_device("cpu", fake_torch())[0] == "cpu"


@pytest.mark.parametrize("args, message", [
    (("nope", "auto", 1), "model"),
    (("htdemucs", "gpu", 1), "device"),
    (("htdemucs", "auto", 0), "shifts"),
])
def test_bad_arguments_are_refused_before_any_work(args, message):
    with pytest.raises(ValueError, match=message):
        stems.check(*args)


def test_keep_two_sums_the_other_stems():
    parts = {name: np.full((4, 2), value) for name, value in
             (("drums", 0.1), ("bass", 0.2), ("vocals", 0.3))}
    two = stems.keep_two(parts, "vocals")
    assert list(two) == ["vocals", "no_vocals"]
    assert np.allclose(two["no_vocals"], 0.3)


def test_level_of_silence_is_floored():
    assert stems.level_db(np.zeros((10, 2))) == -120.0


@pytest.fixture
def stem_tools(monkeypatch, tmp_path):
    calls = []

    def separate_file(path, model, device, shifts, two_stems):
        calls.append((str(path), model, device, shifts, two_stems))
        audio = np.full((441, 2), 0.25, dtype=np.float32)
        return {"stems": {"drums": audio, "vocals": audio * 0}, "rate": 44100,
                "device": "cuda", "device_note": "GPU: Test Card", "seconds": 0.1}

    monkeypatch.setattr(stems, "separate_file", separate_file)
    collector = ToolCollector()
    register_stem_tools(collector)
    return collector.tools, calls


def test_the_tool_writes_one_wav_per_stem(stem_tools, tmp_path):
    tools, calls = stem_tools
    write_wav(tmp_path / "song.wav", np.zeros((100, 2)), 44100)
    result = tools["audio_separate_stems"](str(tmp_path / "song.wav"), two_stems="vocals",
                                           folder=str(tmp_path / "out"))
    assert calls[0][1:] == ("htdemucs", "auto", 1, "vocals")
    assert [s["file"] for s in result["stems"]] == ["song drums.wav", "song vocals.wav"]
    assert result["stems"][1]["level_db"] == -120.0
    assert result["device"] == "cuda"
    samples, rate = read_wav(tmp_path / "out" / "song drums.wav")
    assert rate == 44100 and samples.shape == (441, 2)


def test_the_tool_writes_to_the_browser_folder_by_default(stem_tools, settings_dir, tmp_path):
    tools, _ = stem_tools
    result = tools["audio_separate_stems"](str(tmp_path / "song.wav"))
    assert result["browser_folder"] == "Project bones/FL-MCP stems/song"
    assert (settings_dir.parent / "Projects" / "Project bones" / "FL-MCP stems" / "song"
            / "song vocals.wav").is_file()


def test_the_tool_reports_a_missing_torch(monkeypatch, tmp_path):
    def separate_file(*args):
        raise ImportError(stems.MISSING_TORCH)

    monkeypatch.setattr(stems, "separate_file", separate_file)
    collector = ToolCollector()
    register_stem_tools(collector)
    result = collector.tools["audio_separate_stems"](str(tmp_path / "song.wav"))
    assert "uv sync" in result["error"]
