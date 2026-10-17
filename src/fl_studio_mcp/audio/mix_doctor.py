"""Things worth checking in a mix, from measured stems and the mixer's layout.

Pure rules over numbers that were already measured (audio/analysis.py's output
for the mix, fl_render_stems' summaries for the parts, mixer.getAllTracks for
the routing). Only clipping counts as a problem; everything else is a "check":
something to listen for, which may well be meant. Nothing here knows a genre.
"""

from __future__ import annotations

import math
import re
from itertools import combinations

NEAR_FULL_SCALE_DBFS = -0.3  # a peak this close to 0 dBFS may clip once encoded as MP3
BURIED_LU = 24.0  # a part this far under the loudest may not be heard at all
LOW_BANDS = ("sub", "bass")
LOW_CARRIER_SHARE = 40.0  # percent of a part's energy below 250 Hz: a part that carries the low end
LOW_LEAK_DB = 10.0  # another part's low end this close to a carrier's competes with it
CROWDED_LOW_DB = 6.0  # carriers this close to the loudest carrier share the low end
CROWDED_LOW_PARTS = 3
CLASH_BANDS = ("low_mid", "mid", "high_mid", "high")
CLASH_DB = 3.0  # two parts centred on one band this close in level may mask each other
CLASH_NEAR_LOUDEST_LU = 6.0  # only parts this close to the loudest one are compared
MAX_CLASHES = 3
IGNORED_WORDS = {"insert", "track", "channel", "master", "the", "and", "bus"}
MIN_WORD = 3


def _band_level(stem: dict, bands: tuple[str, ...]) -> float | None:
    """A part's level in the given bands: its loudness scaled by their share."""
    share = sum((stem.get("frequency_percent") or {}).get(band) or 0 for band in bands)
    if share <= 0 or stem.get("loudness_lufs") is None:
        return None
    return stem["loudness_lufs"] + 10 * math.log10(share / 100)


def _finding(severity: str, kind: str, parts: list[str], message: str) -> dict:
    return {"severity": severity, "kind": kind, "parts": parts, "message": message}


def _mix_findings(mix: dict) -> list[dict]:
    clipped = mix.get("clipped_samples") or 0
    if clipped:
        return [_finding("problem", "clipping", ["mix"],
                         f"The mix clips: {clipped} samples at full scale. Bring the master "
                         "down or put a limiter last on it.")]
    peak = mix.get("peak_dbfs")
    if peak is not None and peak > NEAR_FULL_SCALE_DBFS:
        return [_finding("check", "headroom", ["mix"],
                         f"The mix peaks at {peak} dBFS: an MP3 of it may clip. A limiter's "
                         f"ceiling at {NEAR_FULL_SCALE_DBFS} dB or below leaves room.")]
    return []


def _stem_level_findings(stems: list[dict], master_effects: bool) -> list[dict]:
    findings = []
    if not master_effects:
        for stem in stems:
            peak = stem.get("peak_dbfs")
            if peak is not None and peak > 0:
                findings.append(_finding(
                    "problem", "too_hot", [stem["name"]],
                    f"{stem['name']} peaks at +{peak} dBFS before the master's effects: "
                    f"{peak} dB over full scale. Bring its mixer track down."))
    loud = [s for s in stems if s.get("loudness_lufs") is not None]
    if len(loud) > 1:
        loudest = max(loud, key=lambda s: s["loudness_lufs"])
        for stem in loud:
            gap = loudest["loudness_lufs"] - stem["loudness_lufs"]
            if gap >= BURIED_LU:
                findings.append(_finding(
                    "check", "buried", [stem["name"], loudest["name"]],
                    f"{stem['name']} is {gap:.0f} LU under {loudest['name']}: "
                    "is it heard in the mix?"))
    return findings


def _low_end_findings(stems: list[dict]) -> list[dict]:
    levels = {s["name"]: _band_level(s, LOW_BANDS) for s in stems}
    shares = {s["name"]: sum((s.get("frequency_percent") or {}).get(b) or 0 for b in LOW_BANDS)
              for s in stems}
    carriers = [n for n in levels if levels[n] is not None and shares[n] >= LOW_CARRIER_SHARE]
    if not carriers:
        return []
    top = max(carriers, key=lambda n: levels[n])
    findings = []
    for name, level in levels.items():
        if name in carriers or level is None or levels[top] - level > LOW_LEAK_DB:
            continue
        findings.append(_finding(
            "check", "low_end", [name, top],
            f"{name} has low end of its own (below 250 Hz, {shares[name]:.0f}% of its energy) "
            f"only {levels[top] - level:.0f} dB under {top}'s. If it isn't meant to carry "
            "the low end, a high-pass (fl_apply_intent 'high_pass') leaves room for it."))
    crowded = [n for n in carriers if levels[top] - levels[n] <= CROWDED_LOW_DB]
    if len(crowded) >= CROWDED_LOW_PARTS:
        findings.append(_finding(
            "check", "low_end", crowded,
            f"{len(crowded)} parts carry the low end at similar levels ({', '.join(crowded)}): "
            "they add up there. Let one or two of them have it."))
    return findings


def _dominant(stem: dict) -> str | None:
    shares = stem.get("frequency_percent") or {}
    return max(shares, key=lambda b: shares[b] or 0) if shares else None


def _clash_findings(stems: list[dict]) -> list[dict]:
    loud = [s for s in stems if s.get("loudness_lufs") is not None]
    if len(loud) < 2:
        return []
    loudest = max(s["loudness_lufs"] for s in loud)
    near = [s for s in loud if loudest - s["loudness_lufs"] <= CLASH_NEAR_LOUDEST_LU]
    clashes = []
    for first, second in combinations(near, 2):
        band = _dominant(first)
        if band not in CLASH_BANDS or band != _dominant(second):
            continue
        a, b = _band_level(first, (band,)), _band_level(second, (band,))
        if a is not None and b is not None and abs(a - b) <= CLASH_DB:
            clashes.append((abs(a - b), band, first["name"], second["name"]))
    return [
        _finding("check", "masking", [one, other],
                 f"{one} and {other} both have most of their energy in {band} at similar "
                 f"levels ({gap:.1f} dB apart): they may mask each other. Cut one of them "
                 "there, or pan them apart.")
        for gap, band, one, other in sorted(clashes)[:MAX_CLASHES]
    ]


def _words(*names: str) -> set[str]:
    return {w for name in names for w in re.findall(r"[a-z]+", name.lower())
            if len(w) >= MIN_WORD and w not in IGNORED_WORDS}


def _routing_findings(tracks: list[dict], rack: list[dict]) -> list[dict]:
    findings = []
    for channel in rack:
        if channel.get("target_fx_track") == 0 and not channel.get("is_automation"):
            findings.append(_finding(
                "check", "unrouted", [channel["name"]],
                f"{channel['name']} plays straight into the master: it has no mixer track "
                "of its own to be processed or levelled on."))
    direct = [t for t in tracks if t.get("index", 0) > 0 and t.get("routes") == [0]]
    by_word: dict[str, list[str]] = {}
    for track in direct:
        for word in _words(track["name"], *track.get("channels", [])):
            by_word.setdefault(word, []).append(track["name"])
    seen: set[tuple[str, ...]] = set()
    for word, names in sorted(by_word.items()):
        group = tuple(sorted(set(names)))
        if len(group) < 2 or group in seen:
            continue
        seen.add(group)
        findings.append(_finding(
            "check", "grouping", list(group),
            f"{', '.join(group)} (all '{word}') go to the master each on their own: a bus "
            "for them (fl_route_track) lets them be levelled and processed together."))
    return findings


FADER_MIN_DB = -80.0
FADER_MAX_DB = 5.6  # a mixer fader at its top


def gain_plan(stems: list[dict], rack: list[dict], tracks: list[dict],
              target_peak_dbfs: float) -> list[dict]:
    """Per mixer track: the change in dB that brings its loudest stem's peak to the target.

    A track holding several channels is judged by the loudest of them (their sum
    may peak a little higher). Channels on the master itself have no track to trim.
    """
    target = {c["index"]: c.get("target_fx_track") for c in rack}
    levels = {t["index"]: t for t in tracks}
    peaks: dict[int, list[tuple[float, str]]] = {}
    for stem in stems:
        track = target.get(stem.get("index"))
        if stem.get("silent") or stem.get("peak_dbfs") is None or not track:
            continue
        peaks.setdefault(track, []).append((stem["peak_dbfs"], stem["name"]))
    plan = []
    for track, found in sorted(peaks.items()):
        peak, loudest = max(found)
        current = (levels.get(track) or {}).get("volume_db")
        change = round(target_peak_dbfs - peak, 1)
        entry = {"track": track, "name": (levels.get(track) or {}).get("name", f"Insert {track}"),
                 "peak_dbfs": peak, "loudest_channel": loudest, "change_db": change}
        if current is not None:
            wanted = current + change
            entry["volume_db"] = round(current, 1)
            entry["new_volume_db"] = round(min(FADER_MAX_DB, max(FADER_MIN_DB, wanted)), 1)
            if wanted > FADER_MAX_DB:
                entry["note"] = (f"The fader tops out at +{FADER_MAX_DB} dB: raise the "
                                 "channel's own volume or the plugin's output for the rest.")
        plan.append(entry)
    return plan


def diagnose(mix: dict | None = None, stems: list[dict] | None = None,
             tracks: list[dict] | None = None, rack: list[dict] | None = None,
             master_effects: bool = True) -> list[dict]:
    """Findings, problems first. `master_effects`: whether the stems went through them."""
    audible = [s for s in stems or [] if not s.get("silent") and "error" not in s]
    findings = [
        *(_mix_findings(mix) if mix else []),
        *_stem_level_findings(audible, master_effects),
        *_low_end_findings(audible),
        *_clash_findings(audible),
        *_routing_findings(tracks or [], rack or []),
    ]
    return sorted(findings, key=lambda f: f["severity"] != "problem")
