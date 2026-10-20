"""Technical checks of a render before it goes out: what a mastering engineer looks for first.

- DC offset: a channel's mean above DC_LIMIT (about -60 dBFS).
- True peak: the peak between samples, found on a TRUE_PEAK_FACTOR-times
  oversampled signal (FFT upsampling in overlapping chunks, the overlap thrown
  away), as BS.1770 asks; above TRUE_PEAK_LIMIT_DBTP a lossy encode can clip.
- Clicks: a jump in the signal's second difference much larger than around it
  (CLICK_RATIO times the local mean, and at least CLICK_FLOOR), as a cut sample or
  a bad edit leaves. Sharp drum attacks can read as clicks: listen at the times.
- Dropouts: digital silence (below DROPOUT_LEVEL) lasting DROPOUT_SECONDS or more
  inside the audio, not at its start or end.
- Phase: windows of PHASE_WINDOW_SECONDS whose left/right correlation is below
  OUT_OF_PHASE (parts cancel in mono), and how much quieter the mono sum is.
"""

from __future__ import annotations

import numpy as np

DC_LIMIT = 0.001
TRUE_PEAK_FACTOR = 4
TRUE_PEAK_LIMIT_DBTP = -1.0
CHUNK = 1 << 15
OVERLAP = 256
CLICK_RATIO = 12.0
CLICK_FLOOR = 0.05
CLICK_CONTEXT = 1024
CLICK_ISOLATION = 16
CLICK_OWN = 2  # a one-sample spike moves the second difference over three samples
CLICK_GAP_SECONDS = 0.05  # jumps closer than this are one click
DROPOUT_LEVEL = 1e-5
DROPOUT_SECONDS = 0.02
PHASE_WINDOW_SECONDS = 1.0
OUT_OF_PHASE = -0.2
MONO_LOSS_DB = 3.0  # a mono sum this much quieter than the stereo mix is worth a check
LISTED = 20


def _db(value: float) -> float:
    return round(20 * float(np.log10(max(value, 1e-12))), 2)


def true_peak(samples: np.ndarray) -> float:
    """Highest absolute value of the signal oversampled TRUE_PEAK_FACTOR times."""
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    for raw in samples.T:
        channel = _padded(raw)
        for start in range(0, len(channel), CHUNK):
            lo, hi = max(0, start - OVERLAP), min(len(channel), start + CHUNK + OVERLAP)
            piece = channel[lo:hi]
            if len(piece) < 2:
                continue
            up = np.fft.irfft(np.fft.rfft(piece), len(piece) * TRUE_PEAK_FACTOR)
            up *= TRUE_PEAK_FACTOR
            keep_from = (start - lo) * TRUE_PEAK_FACTOR
            keep_to = keep_from + min(CHUNK, len(channel) - start) * TRUE_PEAK_FACTOR
            kept = up[keep_from:keep_to]
            # The padding's own samples aren't the file's: only the file's span counts.
            first = max(0, (OVERLAP - start) * TRUE_PEAK_FACTOR)
            last = min(len(kept), (len(channel) - OVERLAP - start) * TRUE_PEAK_FACTOR)
            if last > first:
                peak = max(peak, float(np.max(np.abs(kept[first:last]))))
    return peak


def _padded(channel: np.ndarray) -> np.ndarray:
    """The channel with OVERLAP samples at each end mirrored through the end sample
    (value and slope carry on), faded to zero.

    The FFT treats each piece as one turn of a loop; at the file's ends the jump
    from its last sample back to its first would ring into the peak (a ramp from
    0.9 to -0.9 read 1.15 without this).
    """
    if len(channel) <= OVERLAP:
        return np.pad(channel, OVERLAP)
    padded = np.pad(channel, OVERLAP, mode="reflect", reflect_type="odd")
    fade = np.linspace(0.0, 1.0, OVERLAP)
    padded[:OVERLAP] *= fade
    padded[-OVERLAP:] *= fade[::-1]
    return padded


def clicks(channel: np.ndarray, rate: int) -> list[float]:
    """Times (seconds) of clicks in one channel.

    A click stands alone: within CLICK_ISOLATION samples around it (its own
    CLICK_OWN-sample footprint aside) no jump reaches a third of it. Seen on the
    user's renders: without that, every kink of a hard-clipped kick read as a
    click (498 in a 3-minute song, 117 with it, all at kick attacks).
    """
    if len(channel) < 3:
        return []
    jump = np.abs(np.diff(channel, 2))
    local = np.convolve(jump, np.ones(CLICK_CONTEXT) / CLICK_CONTEXT, mode="same")
    times: list[float] = []
    for index in np.flatnonzero((jump > CLICK_RATIO * local) & (jump > CLICK_FLOOR)):
        lo = max(0, index - CLICK_ISOLATION)
        around = jump[lo:index + CLICK_ISOLATION + 1].copy()
        around[max(0, index - CLICK_OWN - lo):index + CLICK_OWN + 1 - lo] = 0
        if around.max(initial=0) >= jump[index] / 3:
            continue
        time = (index + 1) / rate
        if not times or time - times[-1] > CLICK_GAP_SECONDS:
            times.append(round(float(time), 3))
    return times


def dropouts(samples: np.ndarray, rate: int) -> list[dict]:
    """[{"start", "seconds"}] of digital silence inside the audio."""
    quiet = np.max(np.abs(samples), axis=1) < DROPOUT_LEVEL
    loud = np.flatnonzero(~quiet)
    if not len(loud):
        return []
    inner = quiet[loud[0]:loud[-1] + 1].astype(int)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], inner, [0]))))
    return [{"start": round((loud[0] + start) / rate, 3),
             "seconds": round((end - start) / rate, 3)}
            for start, end in zip(edges[::2], edges[1::2])
            if (end - start) / rate >= DROPOUT_SECONDS]


def phase(samples: np.ndarray, rate: int) -> dict | None:
    """Out-of-phase windows and the mono sum's loss; None for a mono file."""
    if samples.shape[1] != 2:
        return None
    left, right = samples[:, 0], samples[:, 1]
    width = max(1, int(PHASE_WINDOW_SECONDS * rate))
    bad = []
    for start in range(0, len(left) - width + 1, width):
        a, b = left[start:start + width], right[start:start + width]
        both = np.sqrt(np.mean(a * a) * np.mean(b * b))
        if both > 1e-8 and np.mean(a * b) / both < OUT_OF_PHASE:
            bad.append(round(start / rate, 2))
    stereo = float(np.mean(left ** 2 + right ** 2) / 2)
    mono = float(np.mean(((left + right) / 2) ** 2))
    # A mono sum that cancels completely reads as 60 dB quieter.
    loss = None
    if stereo > 0:
        loss = round(10 * float(np.log10(stereo / max(mono, stereo * 1e-6))), 1)
    return {"out_of_phase_at": bad[:LISTED], "out_of_phase_windows": len(bad),
            "mono_loss_db": loss}


def check(samples: np.ndarray, rate: int) -> dict:
    """All checks; "problems" says in words what is worth a listen."""
    samples = np.asarray(samples, dtype=float)
    if samples.ndim == 1:
        samples = samples[:, None]
    if not len(samples):
        raise ValueError("The audio is empty")
    dc = [round(float(v), 5) for v in samples.mean(axis=0)]
    peak_dbtp = _db(true_peak(samples))
    click_times = sorted({t for channel in samples.T for t in clicks(channel, rate)})
    gaps = dropouts(samples, rate)
    stereo = phase(samples, rate)
    problems = []
    if any(abs(v) > DC_LIMIT for v in dc):
        problems.append(f"DC offset {dc}: a high-pass at 20-30 Hz removes it")
    if peak_dbtp > TRUE_PEAK_LIMIT_DBTP:
        problems.append(f"true peak {peak_dbtp} dBTP, over {TRUE_PEAK_LIMIT_DBTP}: lower the "
                        "limiter's ceiling before an MP3 or streaming encode")
    if click_times:
        problems.append(f"{len(click_times)} possible clicks, first at {click_times[0]} s")
    if gaps:
        problems.append(f"{len(gaps)} dropouts of digital silence, first at {gaps[0]['start']} s")
    if stereo and stereo["out_of_phase_windows"]:
        problems.append(f"out of phase in {stereo['out_of_phase_windows']} second(s), "
                        f"first at {stereo['out_of_phase_at'][0]} s")
    if stereo and stereo["mono_loss_db"] is not None and stereo["mono_loss_db"] >= MONO_LOSS_DB:
        problems.append(f"the mono sum is {stereo['mono_loss_db']} dB quieter: wide or "
                        "phase-shifted parts cancel")
    return {"duration_seconds": round(len(samples) / rate, 2), "dc_offset": dc,
            "true_peak_dbtp": peak_dbtp, "clicks": click_times[:LISTED],
            "click_count": len(click_times), "dropouts": gaps[:LISTED], "phase": stereo,
            "problems": problems}
