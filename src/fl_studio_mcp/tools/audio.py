"""Rendering the project to audio and measuring the result.

`fl_render` drives FL Studio's export dialog (`utils/fl_render.py`);
`fl_render_stems` does it once per channel, heard on its own, to show what each part
contributes; `audio_analyze` measures any WAV file and doesn't touch FL.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fl_studio_mcp.utils.connection import call
from fl_studio_mcp.utils.fl_render import EXPORT_ENTRIES, RenderError, render_audio
from fl_studio_mcp.utils.fl_trigger import keyboard_lock
from fl_studio_mcp.utils.roles import resolve_channel

if TYPE_CHECKING:
    from fastmcp import FastMCP

RENDER_DIR_ENV = "FL_MCP_RENDER_DIR"
DEFAULT_RENDER_TIMEOUT = 300.0
DEFAULT_WINDOW_SECONDS = 4.0
# Inside "Project bones", which FL's browser shows.
SLICE_FOLDER = Path("Projects") / "Project bones" / "FL-MCP slices"
SLICE_BROWSER_FOLDER = "Project bones/FL-MCP slices"
MISSING_NUMPY = (
    "Audio analysis needs numpy, which is an optional dependency: "
    "install it with `uv sync --extra analysis`."
)


def get_render_dir() -> Path:
    """Folder renders go to by default; FL_MCP_RENDER_DIR overrides the temp folder."""
    override = os.environ.get(RENDER_DIR_ENV)
    folder = Path(override) if override else Path(tempfile.gettempdir()) / "fl-studio-mcp"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _new_render_path(prefix: str = "render") -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = get_render_dir() / f"{prefix}-{stamp}.wav"
    number = 2
    while path.exists():
        path = get_render_dir() / f"{prefix}-{stamp}-{number}.wav"
        number += 1
    return path


def analyze_file(path: Path, window_seconds: float, tempo: bool = True) -> dict:
    """Measurements of an audio file (WAV, or any format ffmpeg reads), or {"error": ...}."""
    try:
        from fl_studio_mcp.audio.analysis import analyze
        from fl_studio_mcp.audio.decode import load_audio
    except ImportError:
        return {"error": MISSING_NUMPY}
    if not window_seconds > 0:
        return {"error": f"window_seconds must be more than 0, got {window_seconds}"}
    try:
        samples, rate = load_audio(path)
        return analyze(samples, rate, window_seconds, tempo)
    except (OSError, ValueError) as e:
        return {"error": f"Could not analyse {path}: {e}"}
    except MemoryError:
        return {"error": f"Could not analyse {path}: not enough memory for a file this long"}


# Below this peak a stem counts as silent: the channel plays nothing in what was rendered.
SILENT_PEAK_DBFS = -90.0


def _stem_summary(analysis: dict) -> dict:
    """The few numbers that tell what one channel adds to the mix."""
    if "error" in analysis:
        return {"error": analysis["error"]}
    if analysis["peak_dbfs"] <= SILENT_PEAK_DBFS:
        return {"silent": True}
    return {
        "silent": False,
        "loudness_lufs": analysis["loudness_lufs"],
        "peak_dbfs": analysis["peak_dbfs"],
        "clipped_samples": analysis["clipped_samples"],
        "frequency_percent": {
            band: values["percent"] for band, values in analysis["frequency_balance"].items()
        },
        "stereo_correlation": (analysis.get("stereo") or {}).get("correlation"),  # None: mono
        "key": analysis["key"],
        "pitch": analysis["pitch"],
    }


def _fl(action: str, params: dict | None = None) -> dict:
    """A controller command; raises RenderError if FL can't run it."""
    try:
        result = call(action, params)
    except RuntimeError as e:
        raise RenderError(str(e)) from e
    if "error" in result:
        raise RenderError(result["error"])
    return result


MASTER_TRACK = 0


def render_stems(
    indexes: list[int] | None, timeout: float, keep_files: bool, master_effects: bool = True
) -> dict:
    """Render each channel on its own and summarise it; raises RenderError.

    With `master_effects` off, the master track's effects are switched off for
    the renders and on again afterwards: a limiter there would bring every
    stem up to the same ceiling and hide how loud each part really is.

    A channel is heard on its own by muting every other channel except the
    automation channels that were playing: a muted automation clip stops moving
    its parameter (seen live: a faded lead track rendered at its last fade's
    level, 19 dB too quiet), and they aren't rendered as stems themselves. FL's
    solo isn't used: it mutes the automation too, and with them unmuted again,
    taking the solo off left every other channel muted (seen live). Every
    channel gets back the mute state it had.
    """
    rack = _fl("channels.getAll").get("channels", [])
    missing = [i for i in indexes or [] if i >= len(rack)]
    if missing:
        raise RenderError(
            f"There is no channel {missing[0]}: the rack has channels 0-{len(rack) - 1}"
        )
    wanted = [
        c for c in rack if not c.get("is_muted") and not c.get("is_automation")
    ] if indexes is None else [rack[i] for i in indexes]
    was_muted = {c["index"]: bool(c.get("is_muted")) for c in rack}
    playing_clips = {c["index"] for c in rack if c.get("is_automation") and not c.get("is_muted")}
    bypassed: list[int] = []
    if not master_effects:
        bypassed = [
            effect["slot"]
            for effect in _fl("mixer.getTrackEffects", {"track": MASTER_TRACK}).get("effects", [])
            if effect.get("enabled")
        ]
    stems: list[dict] = []
    muted = dict(was_muted)  # the mute states as they are now
    try:
        for slot in bypassed:
            _fl("mixer.setEffect", {"track": MASTER_TRACK, "slot": slot, "enabled": False})
        for channel in wanted:
            for index in muted:
                mute = index != channel["index"] and index not in playing_clips
                if muted[index] != mute:
                    _fl("channels.mute", {"index": index, "muted": mute})
                    muted[index] = mute
            path = _new_render_path(f"stem-{channel['index']}")
            render_audio(path, timeout)
            summary = _stem_summary(analyze_file(path, DEFAULT_WINDOW_SECONDS, tempo=False))
            if keep_files:
                summary["path"] = str(path)
            else:
                path.unlink(missing_ok=True)
            stems.append({"index": channel["index"], "name": channel.get("name"), **summary})
    finally:
        problems = [
            _put_back("channels.mute", {"index": index, "muted": was_muted[index]})
            for index, mute in muted.items() if mute != was_muted[index]
        ]
        problems.extend(
            _put_back("mixer.setEffect", {"track": MASTER_TRACK, "slot": slot, "enabled": True})
            for slot in bypassed
        )
    result: dict = {"stems": stems}
    if any(problems):
        result["warning"] = (
            "Mute states or the master's effects could not all be put back: "
            + "; ".join(problem for problem in problems if problem)
        )
    return result


def _put_back(action: str, params: dict) -> str | None:
    """Restore a changed state; returns what went wrong instead of raising."""
    try:
        return call(action, params).get("error")
    except RuntimeError as e:
        return str(e)


def register_audio_tools(mcp: FastMCP) -> None:
    """Register render and analysis tools with the MCP server."""

    def _put_selection_back(before: dict) -> None:
        """The playlist's time selection as it was before a render of some bars."""
        try:
            if before.get("active"):
                call("arrangement.setSelection", {"start_bar": before["start_bar"],
                                                  "end_bar": before["end_bar"]})
            else:
                call("arrangement.clearSelection")
        except RuntimeError:
            pass  # FL stopped answering: the render's own result says so

    @mcp.tool()
    def fl_render(
        path: str | None = None,
        analyze: bool = True,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
        start_bar: float | None = None,
        end_bar: float | None = None,
    ) -> dict:
        """Render the project to a WAV or MP3 file and (a WAV, by default) measure it.

        Renders what FL Studio would play: the active pattern in pattern mode,
        the playlist in song mode (see fl_set_loop_mode). It works through FL's
        own export dialog, which flashes up while FL stays in the background; FL
        must not be showing a dialog. The dialog's last-used quality settings
        apply. Windows only.

        Render a .wav to measure the mix, and an .mp3 for the song to keep or
        share (about a tenth of the size): an MP3 can't be measured here.

        Args:
            path: Where to write the .wav or .mp3; it must not exist yet.
                Default: a new .wav in the render folder.
            analyze: Also return the measurements audio_analyze gives (WAV only).
            timeout: Seconds to wait for the render to finish.
            start_bar, end_bar: Render only these bars of the song (end_bar not
                included), e.g. 33 and 49 for a drop. Song mode only; FL renders the
                playlist's time selection, which is set for the render and cleared
                after it.

        Returns {"path", "mode", "render_seconds", "analysis"?} or {"error": ...}.
        """
        target = Path(path) if path else _new_render_path()
        suffix = target.suffix.lower()
        if suffix not in EXPORT_ENTRIES:
            return {"error": f"The render path must end in .wav or .mp3, got {target.name!r}"}
        mode = call("transport.getStatus").get("loop_mode")
        bars = (start_bar, end_bar)
        if bars != (None, None):
            if None in bars:
                return {"error": "Give both start_bar and end_bar, or neither"}
            if mode != "song":
                return {"error": "Rendering some bars needs song mode (fl_set_loop_mode)"}
            before = call("arrangement.getSelection")
            selected = call("arrangement.setSelection",
                            {"start_bar": start_bar, "end_bar": end_bar})
            if "error" in selected:
                return selected
        try:
            seconds = render_audio(target.resolve(), timeout)
        except RenderError as e:
            return {"error": str(e)}
        finally:
            if bars != (None, None):
                _put_selection_back(before)
        result = {"path": str(target.resolve()), "mode": mode, "render_seconds": round(seconds, 1)}
        if bars != (None, None):
            result["bars"] = [start_bar, end_bar]
        if analyze and suffix == ".wav":
            result["analysis"] = analyze_file(target, DEFAULT_WINDOW_SECONDS)
        return result

    @mcp.tool()
    def fl_render_stems(
        channels: list[int | str] | None = None,
        keep_files: bool = False,
        timeout: float = DEFAULT_RENDER_TIMEOUT,
        master_effects: bool = True,
    ) -> dict:
        """Render each channel on its own and measure it, to see what every part adds to the mix.

        Each channel is rendered on its own (every other channel muted, automation
        left playing) like fl_render (pattern or song, per FL's mode), so it is heard
        through its mixer track and the master. Use
        it to balance levels: compare the stems' loudness and where their
        energy sits, change channel or mixer volumes, and render again. Takes
        about as long as one fl_render per channel. Windows only.

        Args:
            channels: Channel indexes or roles to render. Default: every channel
                that isn't muted.
            keep_files: Keep the stem WAVs and return their paths.
            master_effects: False switches the master track's effects off for
                these renders (and on again after). Do that to balance levels
                when the master has a limiter or maximizer: through it every
                loud stem peaks at the same ceiling and the numbers tell
                nothing. A stem peaking above 0 dBFS then is too hot by that
                much.
            timeout: Seconds to wait for each render.

        Returns {"mode", "stems": [{"index", "name", "silent", "loudness_lufs",
        "peak_dbfs", "clipped_samples", "frequency_percent": {band: percent},
        "stereo_correlation", "key", "pitch"}]} or {"error": ...}. A silent stem
        (the channel plays nothing in what was rendered) has only "silent": true.
        "pitch" is the note the stem plays most ({"note", "cents", "share",
        "notes"}, null for unpitched sounds). A part whose key or notes differ
        from what you wrote is tuned away from the notes it is given:
        fl_measure_pitch tells by how much.
        """
        try:
            indexes = None if channels is None else [resolve_channel(c) for c in channels]
        except ValueError as e:
            return {"error": str(e)}
        try:
            with keyboard_lock:
                mode = _fl("transport.getStatus").get("loop_mode")
                result = render_stems(indexes, timeout, keep_files, master_effects)
        except RenderError as e:
            return {"error": str(e)}
        return {"mode": mode, **result}

    @mcp.tool()
    def audio_analyze(path: str, window_seconds: float = DEFAULT_WINDOW_SECONDS) -> dict:
        """Measure an audio file: loudness, peaks, frequency balance, stereo image, key, tempo.

        Doesn't touch FL Studio. WAV is read directly; MP3, FLAC, OGG and other
        formats need ffmpeg (on PATH or FL_MCP_FFMPEG). Returns:
            duration_seconds, sample_rate, channels
            loudness_lufs: integrated programme loudness (ITU-R BS.1770)
            peak_dbfs, rms_dbfs, crest_factor_db (peak minus RMS: low = heavily compressed)
            clipped_samples: samples at full scale
            silence_seconds: silence at the start and end
            frequency_balance: share of energy per band (sub, bass, low_mid, mid,
                high_mid, high) in percent and dB
            stereo: left/right correlation (1 mono, 0 wide, below 0 phase problems)
                and side_to_mid_db (higher = wider)
            key: estimated key with a confidence and the runner-up
            pitch: the note played most of the time ({"note", "midi", "hz", "cents"
                off that note, "share" of the pitched time, "voiced" share of the
                sound that has a pitch, "notes"}); null for unpitched sounds.
                Meant for single sounds and single parts, not for a full mix.
            loudness_over_time: loudness of consecutive windows, to see sections
            tempo: estimated BPM with a confidence and the alternatives (half,
                double, 2/3, 3/2) with their scores; which one is meant can
                depend on the music

        Args:
            path: The audio file.
            window_seconds: Window length for loudness_over_time.
        """
        return analyze_file(Path(path), window_seconds)

    @mcp.tool()
    def audio_compare(path: str, reference: str) -> dict:
        """Measure a mix against a reference track: the same numbers side by side.

        Loudness, peaks, crest factor, the share of energy per frequency band
        (difference in dB), stereo width, key and tempo, and the largest
        differences in words. It tells where the two differ, not which is right.
        Render the mix with fl_render first. Any format audio_analyze reads.

        Returns {"loudness_lufs": {"mix", "reference", "difference"}, ...,
        "largest_differences": [...]} or {"error"}.
        """
        from fl_studio_mcp.audio.compare import compare

        mix = analyze_file(Path(path), DEFAULT_WINDOW_SECONDS)
        if "error" in mix:
            return mix
        ref = analyze_file(Path(reference), DEFAULT_WINDOW_SECONDS)
        if "error" in ref:
            return ref
        return compare(mix, ref)

    @mcp.tool()
    def audio_qc(path: str) -> dict:
        """Technical checks of a render before it goes out. Changes nothing.

        DC offset, true peak (between samples, 4x oversampled; over -1 dBTP an
        MP3 or streaming encode can clip), clicks (sudden jumps such as a cut
        sample leaves; sharp drum hits can read as clicks too, so listen at the
        times), dropouts (digital silence inside the audio), out-of-phase
        seconds and how much quieter the mono sum is. Render with fl_render
        first, or give any audio file.

        Returns {"problems": [in words], "dc_offset", "true_peak_dbtp", "clicks"
        (seconds), "click_count", "dropouts": [{"start", "seconds"}], "phase":
        {"out_of_phase_at", "out_of_phase_windows", "mono_loss_db"}} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.qc import check
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            samples, rate = load_audio(Path(path))
            return check(samples, rate)
        except MemoryError:
            return {"error": f"Not enough memory to check {path}"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not check {path}: {e}"}

    @mcp.tool()
    def audio_match_eq(path: str, reference: str, max_bands: int = 4,
                       strength: float = 0.7) -> dict:
        """EQ moves that bring a mix's tonal balance toward a reference track.

        Compares the two files' long-term spectra in third-octave bands, with
        their overall levels evened out, and turns the largest differences
        (smoothed over an octave, at least 1.5 dB) into EQ bands, at `strength`
        of the difference and at most 6 dB each. A reference is another song:
        check the moves by ear. fl_match_eq renders the mix and can set them.

        Args:
            path: The mix (fl_render's file, or any audio file).
            reference: The track to match.
            max_bands: EQ bands to propose at most (1-7).
            strength: Share of each difference to correct (0-1].

        Returns {"bands": [{"hz", "gain_db", "type"}], "curve": [{"hz",
        "difference_db"}] (reference minus mix)} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.eq_match import match_files
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            return match_files(Path(path), Path(reference), max_bands, strength)
        except MemoryError:
            return {"error": "Not enough memory to compare files this long"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not compare {path} and {reference}: {e}"}

    @mcp.tool()
    def audio_loop_tempo(path: str, beats_per_bar: int = 4) -> dict:
        """The exact tempo of a loop, from its length: a loop is a whole number of bars.

        A number in the file name that fits the length exactly ("SMPL_155_Loop"),
        else the tempo in the name ("128bpm") or the one heard in it, picks how many
        bars it holds; the length then gives the tempo to two decimals.
        Set the project to it (fl_set_tempo) so the loop plays as it is, unstretched.
        Not for whole songs or one-shots.

        Returns {"seconds", "from_name", "estimated", "bars", "bpm", "candidates":
        [{"bars", "bpm"}]} ("bars"/"bpm" null when nothing fits) or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.tempo import estimate_tempo, loop_tempo
            from fl_studio_mcp.utils.sample_tags import parse_bpm
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            samples, rate = load_audio(Path(path))
            seconds = len(samples) / rate
            heard = estimate_tempo(samples.mean(axis=1), rate)
            named = parse_bpm(Path(path).stem)
            guess = named or (heard and heard["bpm"])
            numbers = tuple(float(n) for n in re.findall(r"\d+(?:\.\d+)?", Path(path).stem))
            return {"seconds": round(seconds, 4), "from_name": named,
                    "estimated": heard and heard["bpm"],
                    **loop_tempo(seconds, guess, beats_per_bar, numbers=numbers)}
        except MemoryError:
            return {"error": f"Not enough memory to read {path}"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not read {path}: {e}"}

    @mcp.tool()
    def audio_extract_melody(path: str, bpm: float | None = None, grid: float = 0.25,
                             start_seconds: float = 0.0) -> dict:
        """Turn a recorded melody (one voice or instrument, one note at a time) into notes.

        The result's notes go straight into fl_send_notes (times in beats from
        `start_seconds` into the file, on the grid). The take's overall detune is
        taken out and reported as tuning_cents. Chords and full mixes don't work:
        separate the part first.

        Args:
            path: The audio file (any format audio_analyze reads).
            bpm: The tempo the notes are counted in; default: estimated from the file.
            grid: Grid in beats the notes are put on (0.25 = 16ths).
            start_seconds: Where beat 0 is in the file.

        Returns {"bpm", "tuning_cents", "notes": [{"midi", "note", "time",
        "duration", "velocity"}]} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.melody import extract_melody
            from fl_studio_mcp.audio.tempo import estimate_tempo
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            samples, rate = load_audio(Path(path))
            mono = samples.mean(axis=1)
            if bpm is None:
                found = estimate_tempo(mono, rate)
                if found is None:
                    return {"error": "The file is too short to tell its tempo: give bpm"}
                bpm = found["bpm"]
            return extract_melody(mono, rate, bpm, grid, start_seconds)
        except MemoryError:
            return {"error": f"Could not read a melody from {path}: not enough memory for a "
                             "file this long"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not read a melody from {path}: {e}"}

    @mcp.tool()
    def audio_masking(path_a: str, path_b: str) -> dict:
        """Where two parts compete for the same frequencies (they mask each other).

        Render each part on its own first (fl_render_stems gives the files). A band
        is listed when both parts are active in it and within 6 dB of each other for
        at least a quarter of the time: neither stands out, so both blur there. It
        says where, not which part should give way: cut the one that should sit
        behind (fl_set_track_eq or an EQ effect) and check again.

        Returns {"conflicts": [{"low_hz", "centre_hz", "high_hz", "share",
        "louder" ("a"/"b"/"even"), "difference_db", "level_db"}], "frames"} or
        {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.masking import masking
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            a, rate_a = load_audio(Path(path_a))
            b, rate_b = load_audio(Path(path_b))
            if rate_a != rate_b:
                return {"error": f"The files have different sample rates ({rate_a} and "
                                 f"{rate_b} Hz): render both the same way"}
            return masking(a, b, rate_a)
        except MemoryError:
            return {"error": "Not enough memory to compare files this long"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not compare {path_a} and {path_b}: {e}"}

    @mcp.tool()
    def audio_slice(path: str, sensitivity: float = 0.7, min_length: float = 0.05,
                    max_slices: int = 64, folder: str | None = None) -> dict:
        """Cut a sample or a vocal into slices at its onsets (hits, syllables, chops).

        The sound itself isn't changed: every slice is a plain copy of its part of
        the file, from one onset to the next, written as a WAV. By default they go to
        the browser folder "Project bones/FL-MCP slices/<file name>" (fl_browser_refresh
        then fl_load_in_new_channel loads one). Any format audio_analyze reads.

        Args:
            path: The audio file.
            sensitivity: 0-1; higher keeps quieter onsets (more slices). Measured on
                a 110 BPM drum loop: 0.5 cut it in beats, 0.7 in 8ths, 0.9 in 16ths.
            min_length: Shortest slice in seconds; shorter ones join the one before.
            max_slices: Refuse to write more than this many.
            folder: Where to write them instead.

        Returns {"folder", "browser_folder" (if under Project bones), "slices":
        [{"file", "start", "end"}]} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.slicing import find_onsets, slice_points
            from fl_studio_mcp.audio.wav import write_wav
        except ImportError:
            return {"error": MISSING_NUMPY}
        source = Path(path)
        try:
            samples, rate = load_audio(source)
            onsets = find_onsets(samples.mean(axis=1), rate, sensitivity)
            points = slice_points(onsets, len(samples) / rate, min_length)
        except MemoryError:
            return {"error": f"Not enough memory to slice {path}"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not slice {path}: {e}"}
        if not len(samples):
            return {"error": f"{path} holds no audio"}
        if len(points) > max_slices:
            return {"error": f"{path} would make {len(points)} slices, more than max_slices "
                             f"({max_slices}); lower the sensitivity or raise min_length"}
        if folder is None:
            from fl_studio_mcp.utils.paths import get_fl_settings_dir

            target = (get_fl_settings_dir().parent / SLICE_FOLDER / source.stem)
            browser_folder = f"{SLICE_BROWSER_FOLDER}/{source.stem}"
        else:
            target, browser_folder = Path(folder), None
        try:
            target.mkdir(parents=True, exist_ok=True)
            if folder is None:  # our own folder: the slices of an earlier run go
                for old in target.glob(f"{source.stem} [0-9][0-9].wav"):
                    old.unlink()
            slices = []
            for number, (start, end) in enumerate(points, 1):
                name = f"{source.stem} {number:02d}.wav"
                stop = len(samples) if number == len(points) else round(end * rate)
                write_wav(target / name, samples[round(start * rate):stop], rate)
                slices.append({"file": name, "start": round(start, 4), "end": round(end, 4)})
        except OSError as e:
            return {"error": f"Could not write the slices to {target}: {e}"}
        result: dict = {"folder": str(target), "slices": slices}
        if browser_folder:
            result["browser_folder"] = browser_folder
        return result

    @mcp.tool()
    def audio_chords(path: str, bpm: float | None = None, beats_per_chord: float = 4.0,
                     start_seconds: float = 0.0) -> dict:
        """The chords of a recording over time, one per span of beats (a bar by default).

        Each span's pitch content is matched to major and minor triads on every root:
        a reading of a reference's harmony to write against, not an exact
        transcription (drums and melodies sway it; "confidence" says how clearly a
        chord won, "N" is a span without harmony). Set start_seconds to the first
        downbeat so spans line up with bars. Any format audio_analyze reads.

        Args:
            path: The audio file.
            bpm: Its tempo; default: estimated.
            beats_per_chord: Span length in beats (4 = a bar of 4/4, 2 = half bars).
            start_seconds: Where the first span starts.

        Returns {"bpm", "chords": [{"time" (beats), "chord", "confidence"}],
        "progression": the chords with repeats in a row merged} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.chords import detect_chords
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.tempo import estimate_tempo
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            samples, rate = load_audio(Path(path))
            mono = samples.mean(axis=1)
            if bpm is None:
                found = estimate_tempo(mono, rate)
                if found is None:
                    return {"error": "The file is too short to tell its tempo: give bpm"}
                bpm = found["bpm"]
            chords = detect_chords(mono, rate, bpm, beats_per_chord, start_seconds)
        except MemoryError:
            return {"error": f"Not enough memory to read {path}"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not read the chords of {path}: {e}"}
        merged = [c["chord"] for i, c in enumerate(chords)
                  if i == 0 or c["chord"] != chords[i - 1]["chord"]]
        return {"bpm": bpm, "chords": chords, "progression": merged}

    @mcp.tool()
    def audio_drums(path: str, bpm: float | None = None, grid: float = 0.25,
                    start_seconds: float = 0.0, sensitivity: float = 0.9) -> dict:
        """Turn a drum loop into kick, snare and hat hits on a grid, and read its groove.

        For learning a beat from a loop or a drum stem: "grids" are music_rhythm
        patterns per drum, "notes" go into fl_send_notes (General MIDI: kick 36,
        snare 38, hat 42; move them to your own channels' notes), and "groove" can be
        put on other notes with music_transform [{"op": "groove", "template": ...,
        "step": grid}]. Hits are sorted by where their energy lies (lows: kick, highs:
        hat, the rest: snare), so claps and toms come out as snares. Seen on a
        distorted drum loop: distortion spreads every hit over all frequencies, so
        most came out as snares; use it on clean drums. A full mix confuses it.

        Args:
            path: The audio file (a loop or a drum stem).
            bpm: Its tempo; default: estimated.
            grid: Grid in beats the hits are put on (0.25 = 16ths).
            start_seconds: Where beat 0 is in the file.
            sensitivity: 0-1; higher keeps quieter hits.

        Returns {"bpm", "grid", "hits", "notes", "grids", "groove"} or {"error"}.
        """
        try:
            from fl_studio_mcp.audio.decode import load_audio
            from fl_studio_mcp.audio.drums import transcribe_drums
            from fl_studio_mcp.audio.tempo import estimate_tempo
        except ImportError:
            return {"error": MISSING_NUMPY}
        try:
            samples, rate = load_audio(Path(path))
            mono = samples.mean(axis=1)
            if bpm is None:
                found = estimate_tempo(mono, rate)
                if found is None:
                    return {"error": "The file is too short to tell its tempo: give bpm"}
                bpm = found["bpm"]
            return transcribe_drums(mono, rate, bpm, grid, start_seconds, sensitivity)
        except MemoryError:
            return {"error": f"Not enough memory to read {path}"}
        except (OSError, ValueError) as e:
            return {"error": f"Could not read the drums of {path}: {e}"}
