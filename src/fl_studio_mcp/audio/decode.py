"""Audio files of any format as samples: WAV read directly, anything else through ffmpeg.

ffmpeg (on PATH, or FL_MCP_FFMPEG) decodes MP3, FLAC, OGG and the rest, also
the stock samples that are OGG data in a .wav container, into a temporary
float WAV.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from fl_studio_mcp.audio.wav import read_wav

DECODE_TIMEOUT = 120.0


def find_ffmpeg() -> str | None:
    """The ffmpeg program: FL_MCP_FFMPEG if set, else the one on PATH."""
    configured = os.environ.get("FL_MCP_FFMPEG")
    if configured:
        return configured if Path(configured).is_file() else None
    return shutil.which("ffmpeg")


def load_audio(path: Path) -> tuple[np.ndarray, int]:
    """(samples as frames x channels, sample rate). Raises ValueError or OSError."""
    path = Path(path)
    if not path.is_file():
        raise OSError(f"There is no file {path}")
    wav_problem = ""
    if path.suffix.lower() == ".wav":
        try:
            return read_wav(path)
        except ValueError as e:  # e.g. OGG data in a .wav container: ffmpeg can still read it
            wav_problem = f"{e}; "
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        raise ValueError(
            f"{wav_problem}{path.name} isn't a plain WAV, and reading it needs ffmpeg, which "
            "wasn't found (install it, or set FL_MCP_FFMPEG to the ffmpeg program)"
        )
    with tempfile.TemporaryDirectory(prefix="fl-mcp-decode-") as folder:
        target = Path(folder) / "decoded.wav"
        try:
            done = subprocess.run(
                [ffmpeg, "-v", "error", "-nostdin", "-i", str(path), "-vn",
                 "-acodec", "pcm_f32le", "-f", "wav", str(target)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=DECODE_TIMEOUT, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ValueError(f"ffmpeg took longer than {DECODE_TIMEOUT:g} s to read "
                             f"{path.name}") from None
        if done.returncode != 0 or not target.is_file():
            raise ValueError(f"{wav_problem}ffmpeg could not read {path.name} either: "
                             f"{done.stderr.strip()[:300]}" if wav_problem else
                             f"ffmpeg could not read {path.name}: {done.stderr.strip()[:300]}")
        return read_wav(target)
