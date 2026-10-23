"""EQ moves toward a reference's tonal balance, and setting them on Parametric EQ 2."""

from __future__ import annotations

import numpy as np
import pytest

from fl_studio_mcp.audio.eq_match import MAX_GAIN_DB, long_term_spectrum, match_eq
from fl_studio_mcp.tools import mix_check

RATE = 44100


def noise(seconds=3.0, seed=1):
    return np.random.default_rng(seed).standard_normal(int(seconds * RATE)) * 0.1


def boosted(signal, low, high, db):
    spectrum = np.fft.rfft(signal)
    freqs = np.fft.rfftfreq(len(signal), 1 / RATE)
    spectrum[(freqs >= low) & (freqs < high)] *= 10 ** (db / 20)
    return np.fft.irfft(spectrum, len(signal))


def test_a_reference_with_more_low_mids_gets_a_boost_there():
    mix = noise()
    reference = boosted(noise(seed=2), 200, 400, 8.0) * 3  # louder overall too

    result = match_eq(long_term_spectrum(mix, RATE), long_term_spectrum(reference, RATE))

    top = max(result["bands"], key=lambda b: abs(b["gain_db"]))
    assert 180 <= top["hz"] <= 450 and top["gain_db"] > 2
    assert top["type"] == "Peaking"
    assert all(abs(b["gain_db"]) <= MAX_GAIN_DB for b in result["bands"])


def test_the_same_balance_needs_no_bands_whatever_the_level():
    result = match_eq(long_term_spectrum(noise(), RATE),
                      long_term_spectrum(noise(seed=3) * 5, RATE))

    assert result["bands"] == []


def test_bands_keep_apart_and_strength_scales_them():
    mix = noise()
    reference = boosted(boosted(noise(seed=2), 200, 300, 9), 220, 320, 3)
    full = match_eq(long_term_spectrum(mix, RATE), long_term_spectrum(reference, RATE),
                    strength=1.0)
    half = match_eq(long_term_spectrum(mix, RATE), long_term_spectrum(reference, RATE),
                    strength=0.5)

    centres = [b["hz"] for b in full["bands"]]
    assert all(b / a >= 2 ** (3 / 3) * 0.99 for a, b in zip(centres, centres[1:]))
    assert half["bands"][0]["gain_db"] == pytest.approx(full["bands"][0]["gain_db"] / 2,
                                                        abs=0.15)


@pytest.mark.parametrize("kwargs", [{"max_bands": 0}, {"max_bands": 8}, {"strength": 0}])
def test_bad_arguments_are_refused(kwargs):
    spectrum = long_term_spectrum(noise(), RATE)
    with pytest.raises(ValueError):
        match_eq(spectrum, spectrum, **kwargs)


def test_silence_is_refused():
    with pytest.raises(ValueError, match="silent"):
        match_eq(long_term_spectrum(np.zeros(RATE), RATE), long_term_spectrum(noise(), RATE))


def test_moves_go_onto_free_bands(monkeypatch):
    bands = {1: {"type": "Low shelf", "level": "0.0dB", "freq": "60Hz"},
             2: {"type": "Peaking", "level": "-4.0dB", "freq": "250Hz"},  # in use
             3: {"type": "Peaking", "level": "0.0dB", "freq": "500Hz"},
             4: {"type": "Peaking", "level": "0.1dB", "freq": "1kHz"}}
    set_values = []
    monkeypatch.setattr(mix_check, "get_connection", lambda: object())
    monkeypatch.setattr(mix_check, "find_or_add_plugin", lambda track, plugin: (2, False))
    monkeypatch.setattr(mix_check, "_bands", lambda conn, track, slot: bands)
    monkeypatch.setattr(mix_check, "param_by_name", lambda c, t, s, name: (name, name))
    monkeypatch.setattr(mix_check, "set_param_by_value",
                        lambda c, t, s, name, value: set_values.append((name, value))
                        or {"shows": value})

    done = mix_check.set_eq_bands(0, [{"hz": 80, "gain_db": 2.0, "type": "Low shelf"},
                                      {"hz": 3000, "gain_db": -1.5, "type": "Peaking"}])

    assert [entry["band"] for entry in done["set"]] == [1, 3]
    assert ("Band 3 width", mix_check.EQ_WIDTH) in set_values
    assert ("Band 1 level", "2dB") in set_values and ("Band 3 freq", "3000Hz") in set_values
    with pytest.raises(ValueError, match="free bands"):
        mix_check.set_eq_bands(0, [{"hz": 100 * i, "gain_db": 1, "type": "Peaking"}
                                   for i in range(1, 5)])
