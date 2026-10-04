"""A mixer fader's position (0-1, what its automation stores) and its level in dB.

Measured live (FL 2026, mixer insert 3): the fader set to every 0.025 and its
dB read back. 0.8 is 0 dB, 1.0 is +5.59 dB, 0 is silence. Between the
measured positions the level is taken as straight in dB.
"""

from __future__ import annotations

import math

STEP = 0.025
# dB at positions 0.025, 0.05, ... 1.0 (position 0 is silence).
MEASURED_DB = (
    -42.179, -35.827, -31.969, -29.131, -26.849, -24.918, -23.227, -21.711, -20.329,
    -19.05, -17.854, -16.727, -15.656, -14.633, -13.651, -12.703, -11.785, -10.894,
    -10.026, -9.179, -8.349, -7.536, -6.737, -5.951, -5.177, -4.413, -3.658, -2.912,
    -2.174, -1.443, -0.719, 0.0, 0.713, 1.422, 2.126, 2.826, 3.522, 4.214, 4.904, 5.59,
)
MIN_DB = MEASURED_DB[0]
MAX_DB = MEASURED_DB[-1]


def position_to_db(position: float) -> float:
    """The level of a fader position; -inf at 0."""
    if not 0.0 <= position <= 1.0:
        raise ValueError(f"A fader position runs from 0 to 1, not {position}.")
    if position == 0:
        return -math.inf
    if position < STEP:
        return MIN_DB + 20 * math.log10(position / STEP)
    steps = position / STEP  # 1 .. 40
    index = min(int(steps), len(MEASURED_DB)) - 1
    if index >= len(MEASURED_DB) - 1:
        return MAX_DB
    low, high = MEASURED_DB[index], MEASURED_DB[index + 1]
    return low + (high - low) * (steps - (index + 1))


def db_to_position(db: float) -> float:
    """The fader position giving `db`; raises ValueError above the fader's top."""
    if db > MAX_DB + 1e-6:
        raise ValueError(f"A mixer fader goes up to {MAX_DB} dB, not {db}.")
    if db == -math.inf:
        return 0.0
    if db <= MIN_DB:
        return STEP * 10 ** ((db - MIN_DB) / 20)
    for index in range(len(MEASURED_DB) - 1):
        low, high = MEASURED_DB[index], MEASURED_DB[index + 1]
        if low <= db <= high:
            return STEP * (index + 1 + (db - low) / (high - low))
    return 1.0
