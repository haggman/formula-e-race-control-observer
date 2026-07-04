"""Monotonic, pause-aware, speed-aware replay clock.

Design:
- Wall clock advances monotonically via time.monotonic().
- Race time = (wall elapsed since start) * speed_multiplier - pause_offset.
- Pause freezes the wall reference; resume rebases without losing position.
- Jump rewrites the wall reference so race_time_s = target immediately.
- Speed changes rebase so race_time_s is continuous across the change.
"""
import time
import threading
from typing import Optional


class ReplayClock:
    def __init__(self, start_tick: int, end_tick: int, speed: float = 1.0):
        self._lock = threading.Lock()
        self._start_tick = start_tick
        self._end_tick = end_tick
        self._speed = speed
        self._paused = False
        # _anchor_wall + _anchor_race define the current segment.
        # race_time_s(now) = _anchor_race + (monotonic() - _anchor_wall) * speed
        self._anchor_wall = time.monotonic()
        self._anchor_race = float(start_tick)

    def race_time_s(self) -> float:
        with self._lock:
            if self._paused:
                return self._anchor_race
            elapsed = time.monotonic() - self._anchor_wall
            return self._anchor_race + elapsed * self._speed

    # --- Mutation operations (rebase to keep continuity) ---
    def _rebase_locked(self, new_anchor_race: float) -> None:
        """Reset anchor so race_time_s = new_anchor_race right now."""
        self._anchor_wall = time.monotonic()
        self._anchor_race = new_anchor_race

    def pause(self) -> None:
        with self._lock:
            if not self._paused:
                # Freeze current race_time as anchor
                elapsed = time.monotonic() - self._anchor_wall
                self._anchor_race += elapsed * self._speed
                self._paused = True

    def resume(self) -> None:
        with self._lock:
            if self._paused:
                self._anchor_wall = time.monotonic()
                self._paused = False

    def set_speed(self, multiplier: float) -> None:
        if multiplier <= 0:
            raise ValueError("speed multiplier must be positive")
        with self._lock:
            # Capture current race_time before changing speed
            if self._paused:
                current = self._anchor_race
            else:
                elapsed = time.monotonic() - self._anchor_wall
                current = self._anchor_race + elapsed * self._speed
            self._speed = multiplier
            self._rebase_locked(current)

    def jump(self, target_race_time_s: float) -> None:
        target = max(float(self._start_tick), min(float(self._end_tick), target_race_time_s))
        with self._lock:
            self._rebase_locked(target)

    def restart(self) -> None:
        with self._lock:
            self._rebase_locked(float(self._start_tick))
            self._paused = False

    # --- State accessors ---
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def speed(self) -> float:
        with self._lock:
            return self._speed

    def is_at_end(self) -> bool:
        return self.race_time_s() >= self._end_tick