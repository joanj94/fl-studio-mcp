"""Stem separation tools: a mixed song split into vocals, drums, bass and the rest.

The work is in `audio/stems.py` (Demucs on torch, optional); the stems are written as
WAVs into a browser folder under "Project bones", like audio_slice's slices.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.tools.audio import MISSING_NUMPY

if TYPE_CHECKING:
    from fastmcp import FastMCP

STEM_FOLDER = Path("Projects") / "Project bones" / "FL-MCP stems"
STEM_BROWSER_FOLDER = "Project bones/FL-MCP stems"


def _target(source: Path, folder: str | None) -> tuple[Path, str | None]:
    if folder is not None:
        return Path(folder), None
    from fl_studio_mcp.utils.paths import get_fl_settings_dir

    return (get_fl_settings_dir().parent / STEM_FOLDER / source.stem,
            f"{STEM_BROWSER_FOLDER}/{source.stem}")


def separate_to_files(path: str, model: str = "htdemucs", device: str = "auto",
                      two_stems: str | None = None, shifts: int = 1,
                      folder: str | None = None) -> dict:
    """Separate an audio file and write one WAV per stem. Returns the tool's dict."""
    try:
        from fl_studio_mcp.audio import stems as separation
        from fl_studio_mcp.audio.wav import write_wav
    except ImportError:
        return {"error": MISSING_NUMPY}
    source = Path(path)
    try:
        result = separation.separate_file(source, model, device, shifts, two_stems)
        stems = result["stems"]
    except ImportError as e:
        return {"error": str(e)}
    except MemoryError:
        return {"error": f"Not enough memory to separate {path}: try a shorter file"}
    except (OSError, ValueError, RuntimeError) as e:
        return {"error": f"Could not separate {path}: {e}"}
    target, browser_folder = _target(source, folder)
    try:
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for name, audio in stems.items():
            file = f"{source.stem} {name}.wav"
            write_wav(target / file, audio, result["rate"])
            written.append({"stem": name, "file": file,
                            "level_db": separation.level_db(audio)})
    except OSError as e:
        return {"error": f"Could not write the stems to {target}: {e}"}
    answer: dict = {"folder": str(target), "model": model, "device": result["device"],
                    "device_note": result["device_note"], "seconds": result["seconds"],
                    "rate": result["rate"], "stems": written}
    if browser_folder:
        answer["browser_folder"] = browser_folder
    return answer


def register_stem_tools(mcp: FastMCP) -> None:
    """Register the stem separation tools with the MCP server."""

    @mcp.tool()
    def audio_separate_stems(path: str, model: str = "htdemucs", device: str = "auto",
                             two_stems: str | None = None, shifts: int = 1,
                             folder: str | None = None) -> dict:
        """Split a mixed song into stems with Demucs: vocals, drums, bass, other
        (htdemucs_6s adds guitar and piano). For an acapella to sample, a drum
        break, a bassline to transcribe (audio_extract_melody) or to study a
        reference's parts (audio_analyze on each stem).

        Needs the optional "stems" (CPU) or "stems-gpu" (NVIDIA) extra. The
        first run of a model downloads it (~80 MB). Each stem is a 32-bit float
        WAV at 44.1 kHz in "Project bones/FL-MCP stems/<file name>" by default
        (fl_browser_refresh, then fl_load_in_new_channel). A stem is what the
        model hears: some bleed between them is normal. Any format audio_analyze
        reads.

        Args:
            path: The audio file.
            model: "htdemucs" (default), "htdemucs_ft" (a little cleaner, 4x
                slower), "htdemucs_6s" (6 stems), "mdx_extra".
            device: "auto" (an NVIDIA GPU if one works, then Apple's GPU, else
                the CPU), "cuda", "mps" or "cpu".
            two_stems: e.g. "vocals": only that stem and "no_vocals" (the rest summed).
            shifts: Runs averaged over small time shifts (1-10): cleaner, that
                many times slower.
            folder: Where to write the stems instead.

        Returns {"folder", "browser_folder"?, "model", "device", "device_note",
        "seconds", "rate", "stems": [{"stem", "file", "level_db"}]} or {"error"}.
        A level_db near -60 or lower means that stem is (almost) empty.
        """
        return separate_to_files(path, model, device, two_stems, shifts, folder)

    @mcp.tool()
    def audio_stem_devices() -> dict:
        """What stem separation would run on, without separating anything:
        {"device", "device_note", "models": {name: description}} or {"error"}
        (e.g. Demucs isn't installed, with how to install it)."""
        try:
            from fl_studio_mcp.audio import stems as separation

            device, note = separation.choose_device("auto")
        except ImportError as e:
            return {"error": str(e)}
        return {"device": device, "device_note": note, "models": separation.MODELS}
