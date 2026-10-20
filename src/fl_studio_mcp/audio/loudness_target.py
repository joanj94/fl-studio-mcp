"""The next limiter gain on the way to a loudness target.

Into a limiter, a dB more gain gives less than a dB more loudness, and less the
harder it works. So the first step assumes one for one, and later steps follow the
line through the last two renders (secant method), with the slope kept between
MIN_SLOPE and 1 so a flat reading can't send the gain off. Once renders lie on both
sides of the target, the next gain lies between the closest two (regula falsi):
seen live, a step down from deep limiting found a slope of 0.1 and went 9 dB too far.
"""

from __future__ import annotations

MIN_SLOPE = 0.35  # LU per dB: below this the limiter is crushing; a step stays bounded
MAX_STEP_DB = 12.0


def next_gain(history: list[tuple[float, float]], target_lufs: float) -> float:
    """Gain (dB) to try next, from [(gain dB, measured LUFS), ...] oldest first."""
    if not history:
        raise ValueError("Render once before asking for the next gain")
    below = [h for h in history if h[1] < target_lufs]
    above = [h for h in history if h[1] > target_lufs]
    if below and above:
        (g0, l0) = max(below, key=lambda h: h[1])
        (g1, l1) = min(above, key=lambda h: h[1])
        if abs(l1 - l0) > 1e-6:
            return round(g0 + (target_lufs - l0) * (g1 - g0) / (l1 - l0), 2)
    gain, lufs = history[-1]
    slope = 1.0
    if len(history) >= 2:
        (g0, l0), (g1, l1) = history[-2], history[-1]
        if abs(g1 - g0) > 1e-6:
            slope = min(1.0, max(MIN_SLOPE, (l1 - l0) / (g1 - g0)))
    step = (target_lufs - lufs) / slope
    return round(gain + max(-MAX_STEP_DB, min(MAX_STEP_DB, step)), 2)
