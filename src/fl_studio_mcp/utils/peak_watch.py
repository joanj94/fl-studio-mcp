"""Watching the mixer's meters in the background while FL plays.

A thread asks the controller for the tracks' peaks (`mixer.getPeaks`) over and
over and keeps, per track, the highest peak and how often it went over a level.
FL's API gives no history of the meters, so this is the way to catch a peak
that happens somewhere in a song. Each poll is one controller round trip
(about 25 ms, seen live), so other tools wait a little longer while it runs.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable

DEFAULT_INTERVAL = 0.05
MIN_INTERVAL = 0.02
DEFAULT_OVER_DBFS = -0.3
MAX_SECONDS = 1800.0  # a watch left running stops by itself after half an hour
FLOOR_DBFS = -120.0


def to_dbfs(peak: float) -> float:
    return round(20 * math.log10(peak), 1) if peak > 0 else FLOOR_DBFS


class PeakWatch:
    """One background watch at a time; `send` is the controller call."""

    def __init__(self, send: Callable[[str, dict | None], dict[str, Any]]) -> None:
        self._send = send
        self._lock = threading.Lock()  # the readings
        self._start_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, tracks: list[int], interval: float = DEFAULT_INTERVAL,
              over_dbfs: float = DEFAULT_OVER_DBFS, max_seconds: float = MAX_SECONDS) -> None:
        """Raises ValueError if a watch is running or the arguments are wrong."""
        with self._start_lock:  # two calls at once must not both start a thread
            self._start(tracks, interval, over_dbfs, max_seconds)

    def _start(self, tracks: list[int], interval: float, over_dbfs: float,
               max_seconds: float) -> None:
        if self.running:
            raise ValueError("A peak watch is already running: stop it first")
        if not tracks:
            raise ValueError("Give at least one mixer track to watch")
        if not interval >= MIN_INTERVAL or not 0 < max_seconds <= MAX_SECONDS:
            raise ValueError(f"interval must be at least {MIN_INTERVAL} s and max_seconds "
                             f"0-{MAX_SECONDS:.0f}")
        self._stop.clear()
        self._state = {
            "tracks": {t: {"peak": 0.0, "overs": 0, "first_over_seconds": None} for t in tracks},
            "started": time.monotonic(), "ended": None, "polls": 0, "error": None,
            "over": 10 ** (over_dbfs / 20), "over_dbfs": over_dbfs,
        }
        self._thread = threading.Thread(
            target=self._run, args=(list(tracks), interval, max_seconds),
            name="fl-mcp-peak-watch", daemon=True,
        )
        self._thread.start()

    def _run(self, tracks: list[int], interval: float, max_seconds: float) -> None:
        state = self._state
        try:
            while not self._stop.is_set() and time.monotonic() - state["started"] < max_seconds:
                result = self._send("mixer.getPeaks", {"tracks": tracks})
                if "error" in result:
                    raise RuntimeError(result["error"])
                self._record(result.get("peaks", []))
                self._stop.wait(interval)
        except Exception as e:  # a watch reports what stopped it, it doesn't raise
            with self._lock:
                state["error"] = str(e)
        finally:
            with self._lock:
                state["ended"] = time.monotonic()

    def _record(self, peaks: list[dict]) -> None:
        with self._lock:
            state = self._state
            state["polls"] += 1
            now = round(time.monotonic() - state["started"], 2)
            for entry in peaks:
                track = state["tracks"].get(entry.get("track"))
                if track is None:
                    continue
                peak = float(entry.get("peak") or 0)
                track["peak"] = max(track["peak"], peak)
                if peak >= state["over"]:
                    track["overs"] += 1
                    if track["first_over_seconds"] is None:
                        track["first_over_seconds"] = now

    def status(self) -> dict[str, Any]:
        """What was seen so far ({} before the first start)."""
        with self._lock:
            state = self._state
            if not state:
                return {}
            end = state["ended"] or time.monotonic()
            result = {
                "running": self.running,
                "seconds": round(end - state["started"], 1),
                "polls": state["polls"],
                "over_dbfs": state["over_dbfs"],
                "tracks": [
                    {"track": t, "peak_dbfs": to_dbfs(v["peak"]), "overs": v["overs"],
                     "first_over_seconds": v["first_over_seconds"]}
                    for t, v in state["tracks"].items()
                ],
            }
            if state["error"]:
                result["error"] = state["error"]
            return result

    def stop(self, timeout: float = 2.0) -> dict[str, Any]:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return self.status()
