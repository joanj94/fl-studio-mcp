"""Reading WAV files into numpy arrays.

The standard library's `wave` module rejects 32-bit float WAVs, one of the
formats FL Studio exports, so the RIFF chunks are parsed here.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

_PCM = 1
_FLOAT = 3
_EXTENSIBLE = 0xFFFE


def _chunks(data: bytes) -> dict[bytes, bytes]:
    """RIFF chunk id -> body (first chunk of each kind)."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("Not a WAV file")
    chunks: dict[bytes, bytes] = {}
    position = 12
    while position + 8 <= len(data):
        chunk_id, size = struct.unpack_from("<4sI", data, position)
        body = data[position + 8:position + 8 + size]
        chunks.setdefault(chunk_id, body)
        position += 8 + size + (size & 1)  # chunks are padded to an even length
    return chunks


def _to_float(raw: bytes, format_tag: int, bits: int) -> np.ndarray:
    """Interleaved samples as floats from -1.0 to 1.0."""
    if format_tag == _FLOAT and bits in (32, 64):
        return np.frombuffer(raw, dtype=f"<f{bits // 8}").astype(np.float64)
    if format_tag == _PCM and bits == 8:
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    if format_tag == _PCM and bits in (16, 32):
        return np.frombuffer(raw, dtype=f"<i{bits // 8}").astype(np.float64) / 2.0 ** (bits - 1)
    if format_tag == _PCM and bits == 24:
        data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        values = data[:, 0] | (data[:, 1] << 8) | (data[:, 2] << 16)
        values = np.where(values >= 1 << 23, values - (1 << 24), values)
        return values.astype(np.float64) / 2.0 ** 23
    raise ValueError(f"Unsupported WAV sample format (format {format_tag}, {bits} bits)")


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Samples as an array of shape (frames, channels) in -1.0..1.0, and the sample rate.

    Raises ValueError if the file isn't a WAV this reader understands.
    """
    chunks = _chunks(Path(path).read_bytes())
    if b"fmt " not in chunks or b"data" not in chunks or len(chunks[b"fmt "]) < 16:
        raise ValueError("The WAV file has no format or no audio data")
    fmt = chunks[b"fmt "]
    format_tag, channels, rate, _, block_align, bits = struct.unpack_from("<HHIIHH", fmt)
    if format_tag == _EXTENSIBLE and len(fmt) >= 26:
        format_tag = struct.unpack_from("<H", fmt, 24)[0]  # start of the sub-format GUID
    if channels < 1 or rate < 1 or block_align < 1:
        raise ValueError("The WAV file's format is invalid")

    raw = chunks[b"data"]
    raw = raw[:len(raw) - len(raw) % block_align]  # drop a cut-off last frame
    return _to_float(raw, format_tag, bits).reshape(-1, channels), rate
