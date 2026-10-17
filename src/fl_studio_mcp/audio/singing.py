"""Sung vocals: notes with lyrics written as an OpenUtau project (.ustx) and sung by
`ourender`, a windowless build of OpenUtau's engine (FL_MCP_OURENDER, PATH, or
~/Tools/engine2). The singer and phonemizer are whatever the user installed;
`ourender singers` lists them.

Notes are in fl_send_notes format plus "lyric": a word on its first note, "+"
on each further note of the same word (a melisma). One voice: notes may not overlap.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

RESOLUTION = 480  # ticks per beat, as OpenUtau's own projects
CONTINUE = "+"
RENDER_TIMEOUT = 600.0
RENDER_ATTEMPTS = 2
MAX_BEATS = 100_000  # also keeps NaN and infinity out
LIST_TIMEOUT = 60.0
RENDERERS = {"diffsinger": "DIFFSINGER", "enunu": "ENUNU", "vogen": "VOGEN",
             "voicevox": "VOICEVOX"}
DEFAULT_LOCATIONS = (Path.home() / "Tools" / "engine2" / "ourender.exe",)
NOTE_TAIL = (
    "    pitch: {data: [{x: -40, y: 0, shape: io}, {x: 0, y: 0, shape: io}], snap_first: true}\n"
    "    vibrato: {length: 0, period: 175, depth: 25, in: 10, out: 10, shift: 0, drift: 0, "
    "vol_link: 0}\n"
    "    phoneme_expressions: []\n"
    "    phoneme_overrides: []\n"
)


class SingingError(Exception):
    """The vocal could not be written or sung."""


def find_ourender() -> str | None:
    configured = os.environ.get("FL_MCP_OURENDER")
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("ourender")
    if found:
        return found
    return next((str(p) for p in DEFAULT_LOCATIONS if p.is_file()), None)


def _quoted(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _ticks(beats: float) -> int:
    return round(beats * RESOLUTION)


def renderer_for(singer_type: str) -> str:
    """OpenUtau's renderer for a kind of singer (classic UTAU voicebanks: CLASSIC)."""
    return RENDERERS.get(singer_type.lower(), "CLASSIC")


def ustx_text(name: str, notes: list[dict], bpm: float, beats_per_bar: int, singer: str,
              phonemizer: str, renderer: str = "DIFFSINGER") -> str:
    """The .ustx project. Raises SingingError on bad notes."""
    if not notes:
        raise SingingError("Give the notes to sing")
    if not 10 <= bpm <= 999:
        raise SingingError(f"bpm must be 10-999, got {bpm}")
    if not all(isinstance(n, dict) for n in notes):
        raise SingingError("Every note must be an object with midi, time, duration and lyric")
    try:
        ordered = sorted(notes, key=lambda n: float(n.get("time", 0)))
    except (TypeError, ValueError):
        raise SingingError("Every note's time must be a number of beats") from None
    lines = []
    end = 0
    for number, note in enumerate(ordered):
        try:
            midi, start = int(note["midi"]), float(note.get("time", 0))
            length = float(note["duration"])
        except (KeyError, TypeError, ValueError):
            raise SingingError(f"Note {number + 1} needs midi, time and duration") from None
        lyric = str(note.get("lyric", "")).strip()
        if not lyric:
            raise SingingError(f"Note {number + 1} (beat {start:g}) has no lyric")
        if number == 0 and lyric == CONTINUE:
            raise SingingError("The first note starts a word: it can't be '+'")
        if not 0 <= midi <= 127 or not 0 < length < MAX_BEATS or not 0 <= start < MAX_BEATS:
            raise SingingError(f"Note {number + 1} (beat {start:g}) is out of range")
        if _ticks(start) < end:
            raise SingingError(f"Note {number + 1} (beat {start:g}) overlaps the one before: "
                               "one voice sings one note at a time")
        end = _ticks(start + length)
        lines.append(f"  - position: {_ticks(start)}\n    duration: {_ticks(length)}\n"
                     f"    tone: {midi}\n    lyric: {_quoted(lyric)}\n" + NOTE_TAIL)
    header = (
        f"name: {_quoted(name)}\ncomment: ''\noutput_dir: Vocal\ncache_dir: UCache\n"
        f"ustx_version: '0.7'\nresolution: {RESOLUTION}\nbpm: {bpm:g}\n"
        f"beat_per_bar: {beats_per_bar}\nbeat_unit: 4\n"
        f"time_signatures:\n- {{bar_position: 0, beat_per_bar: {beats_per_bar}, beat_unit: 4}}\n"
        f"tempos:\n- {{position: 0, bpm: {bpm:g}}}\n"
        f"tracks:\n- singer: {_quoted(singer)}\n  phonemizer: {_quoted(phonemizer)}\n"
        f"  renderer_settings: {{renderer: {renderer}}}\n  track_name: {_quoted(name)}\n"
        f"  track_color: Blue\n  mute: false\n  solo: false\n  volume: 0\n  pan: 0\n"
        f"voice_parts:\n- name: {_quoted(name)}\n  comment: ''\n  track_no: 0\n"
        f"  position: 0\n  duration: {end}\n  notes:\n"
    )
    return header + "".join(lines) + "  curves: []\nwave_parts: []\n"


def _run(arguments: list[str], timeout: float) -> subprocess.CompletedProcess:
    program = find_ourender()
    if program is None:
        raise SingingError("ourender wasn't found: set FL_MCP_OURENDER to the program")
    try:
        return subprocess.run([program, *arguments], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise SingingError(f"ourender took longer than {timeout:g} s") from None


def list_singers() -> dict:
    """{"singers": [{"id", "type"}], "phonemizers": [{"id", "name"}]}."""
    done = _run(["singers"], LIST_TIMEOUT)
    if done.returncode != 0:
        raise SingingError(f"ourender failed: {done.stderr.strip()[-300:]}")
    singers, phonemizers = [], []
    for line in done.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "singer" and len(parts) >= 3:
            singers.append({"id": parts[1], "type": parts[2]})
        elif parts[0] == "phonemizer" and len(parts) >= 3:
            phonemizers.append({"id": parts[1], "name": parts[2]})
    return {"singers": singers, "phonemizers": phonemizers}


def sing(project: Path, wav: Path, auto_pitch: bool = True) -> str:
    """Render a .ustx to a WAV; returns ourender's report (phonemes per part)."""
    arguments = ["render", str(project), str(wav)] + (["--auto-pitch"] if auto_pitch else [])
    # Seen live: the pitch model once failed on a thread of its own, and the same
    # project sang fine on the next two runs; so a failed render is tried again.
    for _ in range(RENDER_ATTEMPTS):
        done = _run(arguments, RENDER_TIMEOUT)
        if done.returncode == 0 and wav.is_file():
            return done.stdout.strip()
    hint = " (the singer's pitch model failed; auto_pitch=False avoids it)" if auto_pitch else ""
    raise SingingError(f"ourender could not sing it{hint}: {done.stderr.strip()[-500:]}")
