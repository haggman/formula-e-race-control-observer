"""Pub/Sub publisher with the replay loop (borrowed from the Ch2 simulator).

Publishes one RaceFrame per race-second to the telemetry topic, paced by the
ReplayClock. Speed multipliers >1 publish multiple ticks per wake. Every frame
carries its real ts_utc, so downstream observers can align telemetry with video.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any

from google.cloud import pubsub_v1

from .config import config
from .replay_clock import ReplayClock

logger = logging.getLogger(__name__)


class Publisher:
    def __init__(self, frames: list[dict[str, Any]]):
        self.frames = frames
        self.frame_index = {f["race_time_s"]: f for f in frames}
        self.start_tick = frames[0]["race_time_s"]
        self.end_tick = frames[-1]["race_time_s"]

        self._publisher = pubsub_v1.PublisherClient()
        self._topic_path = self._publisher.topic_path(
            config.PROJECT_ID, config.PUBSUB_TOPIC
        )
        self.clock = ReplayClock(
            start_tick=self.start_tick, end_tick=self.end_tick,
            speed=config.REPLAY_SPEED_MULTIPLIER,
        )
        self.auto_restart = config.AUTO_RESTART_DEFAULT
        self._last_published_tick = self.start_tick - 1
        self._publish_count = 0
        self._error_count = 0
        self._last_error = ""
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="replay-publisher")
        self._thread.start()
        logger.info("publisher started: topic=%s frames=%d ticks=%d..%d",
                    self._topic_path, len(self.frames), self.start_tick, self.end_tick)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def restart_replay(self) -> None:
        self.clock.restart()
        self._last_published_tick = self.start_tick - 1

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick_once()
            except Exception as e:
                self._error_count += 1
                self._last_error = f"{type(e).__name__}: {e}"
                logger.exception("publisher tick failed")
                time.sleep(0.5)

    def _tick_once(self) -> None:
        race_t = self.clock.race_time_s()
        if race_t >= self.end_tick:
            if self.auto_restart:
                self.restart_replay()
            else:
                time.sleep(0.25)
            return
        if self.clock.is_paused():
            time.sleep(0.05)
            return

        target_tick = int(race_t)
        if target_tick > self._last_published_tick:
            for t in range(self._last_published_tick + 1, target_tick + 1):
                frame = self.frame_index.get(t)
                if frame is not None:
                    self._publish_frame(frame)
            self._last_published_tick = target_tick

        time.sleep(max(0.01, 0.5 / max(1.0, self.clock.speed())))

    def _publish_frame(self, frame: dict[str, Any]) -> None:
        data = json.dumps(frame).encode("utf-8")
        future = self._publisher.publish(self._topic_path, data)
        future.add_done_callback(self._publish_callback)
        self._publish_count += 1

    def _publish_callback(self, future) -> None:
        try:
            future.result(timeout=10)
        except Exception as e:
            self._error_count += 1
            self._last_error = f"publish: {type(e).__name__}: {e}"
            logger.exception("publish callback error")

    def status(self) -> dict[str, Any]:
        rt = self.clock.race_time_s()
        span = max(1, self.end_tick - self.start_tick)
        return {
            "race_time_s": round(rt, 2),
            "race_duration_s": self.end_tick - self.start_tick,
            "seconds_remaining": round(max(0, self.end_tick - rt), 2),
            "pct_complete": round(max(0, min(100, (rt - self.start_tick) / span * 100)), 2),
            "last_published_tick": self._last_published_tick,
            "speed_multiplier": self.clock.speed(),
            "paused": self.clock.is_paused(),
            "auto_restart": self.auto_restart,
            "publish_count": self._publish_count,
            "error_count": self._error_count,
            "last_error": self._last_error,
            "topic": self._topic_path,
            "frames_loaded": len(self.frames),
            "start_tick": self.start_tick,
            "end_tick": self.end_tick,
        }
