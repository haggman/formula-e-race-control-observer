"""Agent lifecycle — the 'never run 24/7' guarantee, shared by every agent.

The video observer holds a continuous Gemini Live session, so a forgotten process
would quietly burn tokens. Every agent therefore runs inside a `Session` that
stops itself up to three ways:

  1. Idle watchdog     — the CLOCK GATE. If the stream goes quiet (sim paused /
                          stopped / ended) for longer than the idle timeout, the
                          agent stops working. This ties Gemini activity to the
                          race actually replaying: no frames → no cost.
  2. Graceful stop     — SIGTERM/SIGINT flip the session off cleanly, so when the
                          UI exits it can signal the agents down.
  3. Deadman timeout   — an OPTIONAL hard cap on runtime (`max_runtime_s=None`
                          disables it). Used only for the Gemini spenders (the
                          video observer) as a backstop if the UI dies uncleanly;
                          the deterministic telemetry observer runs without it.

Usage:
    with Session(max_runtime_s=600, idle_timeout_s=45, name="telemetry") as s:
        while s.active():
            item = next_item()           # or a callback calls s.touch()
            if item is None:
                s.wait(0.5); continue
            s.touch()                    # mark activity — resets the idle clock
            handle(item)
    print(s.stop_reason)                 # 'stopped' | 'max_runtime' | 'idle'

`active()` is the single check the agent loops on; it returns False the moment
any stop condition trips, and records why in `stop_reason`.
"""
from __future__ import annotations

import logging
import signal
import threading
import time

logger = logging.getLogger("lifecycle")

DEFAULT_MAX_RUNTIME_S = 600.0        # 10 minutes — the hard deadman cap
DEFAULT_IDLE_TIMEOUT_S = 45.0        # stop if the stream is quiet this long


class Session:
    def __init__(
        self,
        *,
        max_runtime_s: float | None = DEFAULT_MAX_RUNTIME_S,
        idle_timeout_s: float | None = DEFAULT_IDLE_TIMEOUT_S,
        name: str = "agent",
        install_signals: bool = True,
    ):
        self.name = name
        self.max_runtime_s = max_runtime_s
        self.idle_timeout_s = idle_timeout_s
        self._install_signals = install_signals
        self._stop = threading.Event()
        self._start = time.monotonic()
        self._last_activity = self._start
        self.stop_reason: str | None = None
        self._prev_handlers: dict = {}

    # -- lifecycle -----------------------------------------------------------
    def __enter__(self) -> "Session":
        self._start = self._last_activity = time.monotonic()
        if self._install_signals:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    self._prev_handlers[sig] = signal.signal(sig, self._on_signal)
                except ValueError:
                    pass  # not on the main thread — caller handles signals
        logger.info("[%s] session up (max_runtime=%ss, idle_timeout=%ss)",
                    self.name, self.max_runtime_s, self.idle_timeout_s)
        return self

    def __exit__(self, *exc) -> None:
        for sig, handler in self._prev_handlers.items():
            try:
                signal.signal(sig, handler)
            except ValueError:
                pass
        logger.info("[%s] session down (%s) after %.0fs",
                    self.name, self.stop_reason or "stopped", self.elapsed())

    def _on_signal(self, signum, _frame) -> None:
        logger.info("[%s] received signal %s — stopping", self.name, signum)
        self.request_stop("stopped")

    # -- controls ------------------------------------------------------------
    def touch(self) -> None:
        """Mark activity (a frame/message arrived). Resets the idle clock."""
        self._last_activity = time.monotonic()

    def request_stop(self, reason: str = "stopped") -> None:
        if not self._stop.is_set():
            self.stop_reason = reason
            self._stop.set()

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`, waking early if a stop is requested.
        Returns True if still active after the wait."""
        self._stop.wait(seconds)
        return self.active()

    # -- state ---------------------------------------------------------------
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    def idle_for(self) -> float:
        return time.monotonic() - self._last_activity

    def active(self) -> bool:
        if self._stop.is_set():
            return False
        if self.max_runtime_s is not None and self.elapsed() >= self.max_runtime_s:
            self.request_stop("max_runtime")
            logger.info("[%s] max runtime reached — stopping", self.name)
            return False
        if self.idle_timeout_s is not None and self.idle_for() >= self.idle_timeout_s:
            self.request_stop("idle")
            logger.info("[%s] idle for %.0fs — stopping", self.name, self.idle_for())
            return False
        return True
