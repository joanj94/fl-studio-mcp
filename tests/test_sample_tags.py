"""Tempo and key read from sample file names, and filtering a search by them."""

from __future__ import annotations

import pytest

from fl_studio_mcp.utils import browser_index, sample_tags
from tests.test_browser_search import _files


@pytest.mark.parametrize("name, bpm, key", [
    ("Loop_128bpm_F#m", 128.0, "F#m"),
    ("Bass 140 BPM A minor", 140.0, "Am"),
    ("Lead-BPM174-Ebm", 174.0, "D#m"),
    ("Hardcore Kick 040 F", None, "F?"),
    ("Chords Cmaj 90bpm", 90.0, "C"),
    ("Amen Break", None, None),
    ("IMP Cabinet Model D", None, None),
    ("SHP_Vowel A", None, None),
    ("Vowel A minor pad", None, "Am"),
    ("I Am Legend", None, None),
    ("Drop A Beat", None, None),
    ("Bass F 140bpm", 140.0, "F?"),
    ("Pluck Bbm", None, "A#m"),
    ("Keys Db major", None, "C#"),
    ("Lead C Sharp Minor", None, "C#m"),
    ("Pad Eb Maj", None, "D#"),
    ("Am Bass in C", None, "Am"),
    ("Loop 39bpm", None, None),
    ("Loop 300 BPM", 300.0, None),
    ("909 Kick", None, None),
    ("Riser 1000bpm", None, None),
])
def test_tags_from_names(name, bpm, key):
    assert sample_tags.tags(f"Packs/Loops/{name}.wav") == {"bpm": bpm, "key": key}


def test_matcher_checks_tempo_within_tolerance_and_key():
    keep = sample_tags.matcher(bpm=128, key="F#m", bpm_tolerance=1)

    assert keep("a/Loop 128bpm F#m.wav")
    assert keep("a/Loop 127.5 bpm Gbm.wav")
    assert keep("a/Loop 128bpm F#.wav")  # no mode: either mode
    assert not keep("a/Loop 130bpm F#m.wav")
    assert not keep("a/Loop 128bpm A.wav")
    assert not keep("a/Loop F#m.wav")


def test_relative_key_matches_the_relative_major():
    assert not sample_tags.matcher(key="Am")("x/Pad C major.wav")
    assert sample_tags.matcher(key="Am", relative=True)("x/Pad C major.wav")
    assert sample_tags.matcher(key="C", relative=True)("x/Pad Am.wav")


def test_no_filter_is_none_and_bad_keys_are_refused():
    assert sample_tags.matcher() is None
    with pytest.raises(ValueError, match="H"):
        sample_tags.matcher(key="H minor")


def test_search_with_only_a_filter(tmp_path):
    user = tmp_path / "User"
    _files(user, "Presets/Loops/Top 150bpm Am.wav", "Presets/Loops/Top 128bpm Am.wav",
           "Presets/Loops/Bass 150 BPM C.wav")
    roots = browser_index.browser_roots(None, user)

    matches, total = browser_index.search(roots, "", keep=sample_tags.matcher(bpm=150))

    assert total == 2
    assert set(matches) == {"Loops/Top 150bpm Am.wav", "Loops/Bass 150 BPM C.wav"}
    with pytest.raises(ValueError):
        browser_index.search(roots, "")


@pytest.mark.parametrize("spelling", ["Db major", "A minor", "C sharp", "am", " F#m "])
def test_the_filter_reads_spelled_out_keys(spelling):
    assert sample_tags.matcher(key=spelling) is not None


def test_spelled_out_keys_match_their_files():
    assert sample_tags.matcher(key="A minor")("x/Loop Am.wav")
    assert sample_tags.matcher(key="Db major")("x/Keys C# 120bpm.wav")
    assert not sample_tags.matcher(key="Db major")("x/Keys C#m.wav")
