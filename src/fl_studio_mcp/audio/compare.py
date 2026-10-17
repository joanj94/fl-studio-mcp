"""A mix measured against a reference track: the same numbers side by side.

It tells where two pieces differ, not which is right: a difference in a band
is a choice to check by ear, never a fault on its own.
"""

from __future__ import annotations

import math

LISTED_DIFFERENCES = 3
NOTABLE_DB = 1.5  # band differences smaller than this aren't listed


def _pair(mix: float | None, reference: float | None, digits: int = 1) -> dict:
    pair = {"mix": mix, "reference": reference}
    if mix is not None and reference is not None:
        pair["difference"] = round(mix - reference, digits)
    return pair


def compare(mix: dict, reference: dict) -> dict:
    """Side-by-side measurements of two analyses (audio/analysis.py's output)."""
    bands = {}
    for band, values in mix["frequency_balance"].items():
        ref = reference["frequency_balance"].get(band, {})
        share, ref_share = values.get("percent"), ref.get("percent")
        entry = {"mix_percent": share, "reference_percent": ref_share}
        if share and ref_share:
            entry["difference_db"] = round(10 * math.log10(share / ref_share), 1)
        bands[band] = entry
    notable = sorted(
        (b for b in bands.items() if abs(b[1].get("difference_db", 0)) >= NOTABLE_DB),
        key=lambda b: -abs(b[1]["difference_db"]),
    )[:LISTED_DIFFERENCES]
    differences = [
        f"{band}: {abs(e['difference_db'])} dB {'more' if e['difference_db'] > 0 else 'less'} "
        "of the energy than in the reference"
        for band, e in notable
    ]
    loudness = _pair(mix.get("loudness_lufs"), reference.get("loudness_lufs"))
    if "difference" in loudness and abs(loudness["difference"]) >= 1:
        word = "louder" if loudness["difference"] > 0 else "quieter"
        differences.insert(0, f"overall: {abs(loudness['difference'])} LU {word}")
    stereo = (mix.get("stereo") or {}), (reference.get("stereo") or {})
    return {
        "loudness_lufs": loudness,
        "peak_dbfs": _pair(mix.get("peak_dbfs"), reference.get("peak_dbfs")),
        "crest_factor_db": _pair(mix.get("crest_factor_db"), reference.get("crest_factor_db")),
        "frequency_balance": bands,
        "stereo_correlation": _pair(stereo[0].get("correlation"), stereo[1].get("correlation"),
                                    2),
        "side_to_mid_db": _pair(stereo[0].get("side_to_mid_db"),
                                stereo[1].get("side_to_mid_db")),
        "key": {"mix": mix.get("key"), "reference": reference.get("key")},
        "tempo": {"mix": (mix.get("tempo") or {}).get("bpm"),
                  "reference": (reference.get("tempo") or {}).get("bpm")},
        "largest_differences": differences,
    }
