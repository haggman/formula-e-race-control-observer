"""Agent heartbeats → Firestore, so the console can show which agents are online.

Each agent writes a small doc to `agent_status/{name}` every few seconds with its
state (and a detail string, e.g. the video verifier's warm-up progress). The
console polls that collection and lights up a status dot at the top of each feed.
An agent is "online" if its heartbeat is recent; "warming" carries progress.
"""
from __future__ import annotations

import os
import threading
import time


class Heartbeat:
    """Periodic Firestore heartbeat for one agent (background thread)."""

    def __init__(self, name: str, *, project: str | None = None, interval: float = 5.0):
        self.name = name
        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.interval = interval
        self._state = "starting"
        self._detail = ""
        self._db = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def set(self, state: str, detail: str = "") -> None:
        """Update the state/detail and write immediately."""
        self._state, self._detail = state, detail
        self._write()

    def _write(self) -> None:
        try:
            if self._db is None:
                from google.cloud import firestore
                self._db = firestore.Client(project=self.project)
            self._db.collection("agent_status").document(self.name).set({
                "name": self.name, "state": self._state, "detail": self._detail,
                "updated_at_unix": int(time.time()),
            })
        except Exception:
            pass                                    # heartbeats are best-effort

    def start(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"hb-{self.name}")
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self._write()                           # keep-alive

    def stop(self) -> None:
        self._stop.set()
        self.set("offline")
