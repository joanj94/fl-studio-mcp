"""Splitting a song into stems (vocals, drums, bass, other) with Demucs.

Demucs (Meta's hybrid transformer model) runs on torch, an optional dependency
(the "stems" or "stems-gpu" extra). The device is found at run time: an NVIDIA card
when torch was built with CUDA and the card runs a small test (a torch build can
lack the card's kernels: it then reports CUDA and fails at the first operation),
Apple's GPU (MPS), else the CPU. A GPU that runs out of memory falls back to the CPU
when the device was left to "auto".

The stems are what the model hears, not the song's own tracks: their sum is close
to the mix, each one carries some of the others. The sound is otherwise not
changed (no stretching or retuning).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np

MODELS = {
    "htdemucs": "4 stems, the default; about a song's length on a CPU, seconds on a GPU",
    "htdemucs_ft": "4 stems, a little cleaner; four times slower",
    "htdemucs_6s": "6 stems: adds guitar and piano (piano is weak)",
    "mdx_extra": "4 stems, the older MDX model",
}
DEVICES = ("auto", "cpu", "cuda", "mps")
MISSING_TORCH = (
    "Stem separation needs Demucs and torch, which are optional: install them with "
    "`uv sync --extra analysis --extra stems-gpu` (NVIDIA card) or "
    "`uv sync --extra analysis --extra stems` (CPU only), then restart the server."
)
MAX_SHIFTS = 10

_loaded: dict[str, object] = {}


def _torch():
    try:
        import torch
    except ImportError:
        raise ImportError(MISSING_TORCH) from None
    return torch


def _cuda_works(torch) -> str | None:
    """None if a small CUDA job runs, else why not."""
    if not torch.cuda.is_available():
        if torch.version.cuda is None:
            return "this torch is built for the CPU only (install the stems-gpu extra)"
        return "torch finds no CUDA card or driver"
    try:
        x = torch.ones((1, 2, 64), device="cuda")
        torch.nn.functional.conv1d(x, torch.ones((2, 2, 3), device="cuda")).sum().item()
    except RuntimeError as e:  # e.g. "no kernel image is available for execution"
        return f"the card can't run this torch build: {e}"
    return None


def _mps_works(torch) -> bool:
    backend = getattr(torch.backends, "mps", None)
    return bool(backend and backend.is_available())


def choose_device(requested: str = "auto", torch=None) -> tuple[str, str]:
    """(device, why): the device to run on and a note on how it was chosen.

    "auto" takes CUDA, then MPS, then the CPU. A device asked for by name that
    doesn't work raises ValueError with the reason.
    """
    if requested not in DEVICES:
        raise ValueError(f"device must be one of {', '.join(DEVICES)}, got {requested!r}")
    torch = torch or _torch()
    if requested == "cpu":
        return "cpu", "CPU as asked"
    if requested in ("auto", "cuda"):
        problem = _cuda_works(torch)
        if problem is None:
            return "cuda", f"GPU: {torch.cuda.get_device_name(0)}"
        if requested == "cuda":
            raise ValueError(f"CUDA can't be used: {problem}")
        if _mps_works(torch):
            return "mps", f"Apple GPU (no CUDA: {problem})"
        return "cpu", f"CPU (no GPU: {problem})"
    if not _mps_works(torch):
        raise ValueError("MPS (Apple GPU) isn't available in this torch")
    return "mps", "Apple GPU as asked"


def load_model(name: str):
    """The pretrained model, kept for later calls (downloaded once, ~80 MB per model)."""
    if name not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}, got {name!r}")
    if name not in _loaded:
        _torch()
        # The Hugging Face cache copies instead of linking on Windows without Developer
        # Mode, and says so on every download.
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        from demucs.pretrained import get_model

        _loaded[name] = get_model(name)
    return _loaded[name]


def _prepare(samples: np.ndarray, rate: int, model, torch):
    """Samples (frames x channels) as the model's channels x frames at its rate."""
    import julius

    wav = torch.from_numpy(np.ascontiguousarray(np.asarray(samples, dtype=np.float32).T))
    if wav.shape[0] == 1:
        wav = wav.expand(model.audio_channels, -1)
    elif wav.shape[0] > model.audio_channels:
        wav = wav[:model.audio_channels]
    if rate != model.samplerate:
        wav = julius.resample_frac(wav, rate, model.samplerate)
    return wav.contiguous()


def _run(model, wav, device: str, shifts: int, torch):
    from demucs.apply import apply_model

    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    with torch.no_grad():
        out = apply_model(model, ((wav - mean) / std)[None], shifts=shifts, split=True,
                          overlap=0.25, device=device, progress=False)
    return out[0] * std + mean


def keep_two(stems: dict[str, np.ndarray], stem: str) -> dict[str, np.ndarray]:
    """`stem` and "no_<stem>" (the sum of all the others)."""
    if stem not in stems:
        raise ValueError(f"two_stems must be one of {', '.join(stems)}, got {stem!r}")
    rest = sum(audio for name, audio in stems.items() if name != stem)
    return {stem: stems[stem], f"no_{stem}": rest}


def separate(samples: np.ndarray, rate: int, model_name: str = "htdemucs",
             device: str = "auto", shifts: int = 1, two_stems: str | None = None) -> dict:
    """Separate audio (frames x channels). Returns {"stems": {name: frames x channels},
    "rate", "device", "device_note", "seconds"}. Raises ValueError/ImportError.

    two_stems keeps that stem and "no_<stem>" (keep_two)."""
    check(model_name, device, shifts)
    if len(samples) < rate:
        raise ValueError("The audio is shorter than a second")
    torch = _torch()
    chosen, note = choose_device(device, torch)
    model = load_model(model_name)
    if two_stems is not None and two_stems not in model.sources:
        raise ValueError(f"two_stems must be one of {', '.join(model.sources)}, "
                         f"got {two_stems!r}")
    wav = _prepare(samples, rate, model, torch)
    started = time.monotonic()
    try:
        out = _run(model, wav, chosen, shifts, torch)
    except torch.cuda.OutOfMemoryError:
        if device != "auto":
            raise ValueError("The GPU ran out of memory: try device='cpu'") from None
        torch.cuda.empty_cache()
        chosen, note = "cpu", f"CPU (the GPU ran out of memory; {note})"
        out = _run(model, wav, chosen, shifts, torch)
    stems = {name: out[i].cpu().numpy().T for i, name in enumerate(model.sources)}
    if two_stems is not None:
        stems = keep_two(stems, two_stems)
    return {"stems": stems, "rate": int(model.samplerate), "device": chosen,
            "device_note": note, "seconds": round(time.monotonic() - started, 1)}


def check(model_name: str, device: str, shifts: int) -> None:
    """ValueError for arguments that can be judged before any work."""
    if model_name not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}, got {model_name!r}")
    if device not in DEVICES:
        raise ValueError(f"device must be one of {', '.join(DEVICES)}, got {device!r}")
    if not isinstance(shifts, int) or not 1 <= shifts <= MAX_SHIFTS:
        raise ValueError(f"shifts must be 1-{MAX_SHIFTS}, got {shifts}")


def level_db(audio: np.ndarray) -> float:
    """RMS level in dBFS (-120 for silence)."""
    rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
    return round(20 * float(np.log10(max(rms, 1e-6))), 1)


def separate_file(path: Path, model_name: str = "htdemucs", device: str = "auto",
                  shifts: int = 1, two_stems: str | None = None) -> dict:
    """separate() on an audio file in any format load_audio reads."""
    from fl_studio_mcp.audio.decode import load_audio

    check(model_name, device, shifts)
    samples, rate = load_audio(Path(path))
    return separate(samples, rate, model_name, device, shifts, two_stems)
