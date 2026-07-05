"""SimClock — the shared race clock, read from the simulator's /status.

The telemetry simulator owns the authoritative ReplayClock; it exposes
`race_time_s` on /status. Both observers key off it, which is what keeps video and
telemetry in sync without any second clock. The video observer also uses it as the
on/off switch: when race_time_s stops advancing (sim paused/ended), the observer
closes its Gemini Live session.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class ClockSample:
    race_time_s: float | None
    reachable: bool


class SimClock:
    """Polls the simulator's /status for race_time_s and tracks advancement."""

    def __init__(self, sim_url: str, stale_after_s: float = 3.0):
        self.sim_url = sim_url.rstrip("/")
        self.stale_after_s = stale_after_s
        self._last_value: float | None = None
        self._last_change_wall: float = time.monotonic()

    def read(self) -> ClockSample:
        """Fetch race_time_s once. Records when the value last changed so callers
        can tell 'advancing' from 'paused/ended'."""
        import urllib.request
        import json
        try:
            with urllib.request.urlopen(f"{self.sim_url}/status", timeout=5) as r:
                rt = float(json.loads(r.read()).get("race_time_s"))
        except Exception:
            return ClockSample(race_time_s=None, reachable=False)

        if rt != self._last_value:
            self._last_value = rt
            self._last_change_wall = time.monotonic()
        return ClockSample(race_time_s=rt, reachable=True)

    def is_advancing(self) -> bool:
        """True if race_time_s has changed within the staleness window."""
        return (time.monotonic() - self._last_change_wall) < self.stale_after_s
