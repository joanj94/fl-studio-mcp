"""Sung vocals made outside FL Studio and brought in as a sample.

`vocal_render` writes the notes and lyrics as an OpenUtau project and has the
user's installed singer sing it (`audio/singing.py`); the WAV goes where the
browser can find it, to be loaded like any sample.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.audio import singing
from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.paths import get_fl_settings_dir

if TYPE_CHECKING:
    from fastmcp import FastMCP

# Inside "Project bones", which FL's browser shows.
VOCAL_FOLDER = Path("Projects") / "Project bones" / "FL-MCP vocals"
VOCAL_BROWSER_FOLDER = "Project bones/FL-MCP vocals"
DEFAULT_PHONEMIZERS = {"diffsinger": "OpenUtau.Core.DiffSinger.DiffSingerEnglishPhonemizer"}


def _vocal_dir() -> Path:
    return get_fl_settings_dir().parent / VOCAL_FOLDER


def register_vocal_tools(mcp: FastMCP) -> None:
    """Register the sung-vocal tools with the MCP server."""

    @mcp.tool()
    def vocal_list_singers() -> dict:
        """The singers and phonemizers (languages) installed for vocal_render.

        Returns {"singers": [{"id", "type"}], "phonemizers": [{"id", "name"}]} or {"error"}.
        """
        try:
            return singing.list_singers()
        except singing.SingingError as e:
            return {"error": str(e)}

    @mcp.tool()
    def vocal_render(
        notes: list[dict],
        name: str,
        bpm: float | None = None,
        singer: str | None = None,
        phonemizer: str | None = None,
        auto_pitch: bool = True,
        beats_per_bar: int = 4,
    ) -> dict:
        """Have a synthesized singer sing a melody with lyrics, as a WAV to load into FL.

        The singer is one the user installed for OpenUtau (vocal_list_singers).
        The WAV goes to the browser folder "Project bones/FL-MCP vocals"; load it
        with fl_load_in_new_channel (fl_browser_refresh first if it isn't listed)
        and play it from beat 0 of a pattern, like any vocal sample. A sample
        already loaded in FL keeps its old sound after a new render under the
        same name: load it again. Takes about a second per sung note.

        Args:
            notes: In fl_send_notes format (times in beats from the vocal's start)
                plus "lyric": the word on its first note, "+" on each further note
                of the same word. One voice: notes may not overlap.
            name: File name for the WAV (and its .ustx project), e.g. "verse 1".
            bpm: Tempo; default: the FL project's.
            singer: A singer id; default: the first one installed.
            phonemizer: A phonemizer id (the lyrics' language); default for a
                DiffSinger singer: English.
            auto_pitch: Let the singer's own pitch model shape the notes (natural
                slides and vibrato); off, every note is sung flat.
            beats_per_bar: Time signature numerator.

        Returns {"path", "browser_path", "ustx", "seconds", "report"} or {"error"}.
        """
        try:
            if bpm is None:
                tempo = call("project.getTempo")
                if "error" in tempo:
                    return {"error": f"Give bpm (FL's tempo couldn't be read: {tempo['error']})"}
                bpm = tempo["bpm"]
            installed = singing.list_singers()["singers"]
            if not installed:
                return {"error": "No singer is installed for OpenUtau"}
            chosen = next((s for s in installed if singer in (None, s["id"])), None)
            if chosen is None:
                return {"error": f"No singer {singer!r}; installed: "
                                 f"{', '.join(s['id'] for s in installed)}"}
            voice = phonemizer or DEFAULT_PHONEMIZERS.get(chosen["type"].lower())
            if voice is None:
                return {"error": "Give the phonemizer for this singer (vocal_list_singers)"}
            safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
            if not any(c.isalnum() for c in safe):
                return {"error": f"name must have letters or digits, got {name!r}"}
            folder = _vocal_dir()
            folder.mkdir(parents=True, exist_ok=True)
            project, wav = folder / f"{safe}.ustx", folder / f"{safe}.wav"
            project.write_text(singing.ustx_text(
                name, notes, bpm, beats_per_bar, chosen["id"], voice,
                singing.renderer_for(chosen["type"])), encoding="utf-8")
            wav.unlink(missing_ok=True)
            start = time.monotonic()
            report = singing.sing(project, wav, auto_pitch)
        except (singing.SingingError, OSError, RuntimeError) as e:
            return {"error": str(e)}
        return {"path": str(wav), "browser_path": f"{VOCAL_BROWSER_FOLDER}/{wav.name}",
                "ustx": str(project), "seconds": round(time.monotonic() - start, 1),
                "report": report}
