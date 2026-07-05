"""Telemetry Observer — the deterministic stream consumer (Agent 2).

Subscribes to the telemetry Pub/Sub stream, keeps a short rolling window per car,
runs the deterministic detector, and emits Observations. NO Gemini — pure math,
so it's cheap to run; it's the reference implementation of the shared lifecycle
(deadman timeout / graceful stop / idle watchdog) that the video observer reuses.

On startup it SEEKS its subscription to 'now', so a run only ever reacts to live
frames — never a stale backlog that piled up while the observer was off.

Lifecycle: this observer is deterministic (no Gemini), so it runs with NO deadman
cap. It's CLOCK-GATED instead — the idle watchdog stops it when the stream goes
quiet (sim paused/stopped/ended), and a UI SIGTERM stops it cleanly. The deadman
cap is reserved for the video observer (the Gemini spender).

The windowing + detection logic (`TelemetryObserver`) is separated from the
Pub/Sub transport so it can be tested offline against a frame replay.

Run (after `source activate.sh`, with the simulator publishing):
    python -m observers.telemetry.consumer            # runs while the race streams
    python -m observers.telemetry.consumer --max-runtime 120   # optional test cap
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.models import Observation, RaceFrame, SignalType, TelemetrySample  # noqa: E402
from shared.lifecycle import Session                                           # noqa: E402
from observers.telemetry import detector                                       # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("telemetry.observer")

WINDOW_S = 8.0            # rolling per-car window the detector scores
DEBOUNCE_S = 10.0        # suppress repeats of the same (car, signal)


class TelemetryObserver:
    """Rolling-window detector over the telemetry stream. Transport-agnostic."""

    def __init__(self, window_s: float = WINDOW_S, debounce_s: float = DEBOUNCE_S):
        self.window_s = window_s
        self.debounce_s = debounce_s
        self._buf: dict[int, list[TelemetrySample]] = {}
        self._stopped: dict[int, bool] = {}
        self._last_fired: dict[tuple[int, str], float] = {}
        self._stop_since: dict[int, float] = {}       # car -> stop start epoch
        self._escalated: dict[int, bool] = {}         # car -> PROLONGED_STOP emitted

    def process_frame(self, frame: RaceFrame) -> list[Observation]:
        """Fold one frame into the per-car windows and return any new Observations."""
        out: list[Observation] = []
        now = frame.ts_utc.timestamp()
        for s in frame.to_samples():
            car = s.car_number
            buf = self._buf.setdefault(car, [])
            buf.append(s)
            cutoff = s.ts_utc.timestamp() - self.window_s
            while buf and buf[0].ts_utc.timestamp() < cutoff:
                buf.pop(0)

            for obs in detector.detect(buf, already_stopped=self._stopped.get(car, False)):
                if obs.signal == SignalType.STOPPED_CAR:
                    self._stopped[car] = True
                    self._stop_since.setdefault(car, obs.ts_utc.timestamp())
                key = (car, obs.signal.value)
                if now - self._last_fired.get(key, -1e9) < self.debounce_s:
                    continue                       # caller-owned debounce
                self._last_fired[key] = now
                out.append(obs)

            # Persistence escalation: still stopped past the escalate hold →
            # PROLONGED_STOP (a confirmed blockage → Safety Car on telemetry alone,
            # not waiting on the slow video corroboration).
            if (self._stopped.get(car) and not self._escalated.get(car)
                    and car in self._stop_since
                    and now - self._stop_since[car] >= detector.STOP_ESCALATE_S):
                self._escalated[car] = True
                out.append(_prolonged_stop_obs(s, now - self._stop_since[car]))

            # release the stop latch once the car is clearly moving again
            if buf and buf[-1].speed_kmh > 30:
                self._stopped[car] = False
                self._stop_since.pop(car, None)
                self._escalated[car] = False
        return out


def _prolonged_stop_obs(s: TelemetrySample, held_s: float) -> Observation:
    """Build the PROLONGED_STOP escalation Observation for a still-stopped car."""
    from shared.models import Modality, SignalType, TrackLocation
    return Observation(
        modality=Modality.TELEMETRY, signal=SignalType.PROLONGED_STOP,
        ts_utc=s.ts_utc, car_number=s.car_number,
        confidence=detector.PROLONGED_CONF, severity_hint=detector.SEV_PROLONGED,
        location=TrackLocation(gps_lat=s.lat, gps_lng=s.lng),
        summary=f"car {s.car_number} STILL stopped after {held_s:.0f}s — confirmed blockage",
        evidence={"held_s": round(held_s, 1)},
    )


# ---------------------------------------------------------------------------
# Pub/Sub transport
# ---------------------------------------------------------------------------

def _emit_print(obs: Observation) -> None:
    print(json.dumps({
        "ts_utc": obs.ts_utc.isoformat(), "signal": obs.signal.value,
        "car": obs.car_number, "conf": obs.confidence, "sev": obs.severity_hint,
        "summary": obs.summary,
    }))


def _ensure_subscription_seek_now(subscriber, project: str, topic: str, sub: str) -> str:
    """Create the pull subscription if missing, then seek it to now (drop backlog)."""
    from google.api_core import exceptions
    from google.protobuf.timestamp_pb2 import Timestamp

    sub_path = subscriber.subscription_path(project, sub)
    topic_path = f"projects/{project}/topics/{topic}"
    try:
        subscriber.create_subscription(request={
            "name": sub_path, "topic": topic_path, "ack_deadline_seconds": 30,
            "message_retention_duration": {"seconds": 600},
        })
        logger.info("created subscription %s", sub)
    except exceptions.AlreadyExists:
        pass
    ts = Timestamp(); ts.FromDatetime(datetime.now(timezone.utc))
    subscriber.seek(request={"subscription": sub_path, "time": ts})
    logger.info("sought %s to now — only live frames from here", sub)
    return sub_path


def run(
    *,
    project: Optional[str] = None,
    topic: str = "fe-telemetry",
    subscription: str = "fe-telemetry-observer-sub",
    max_runtime_s: float | None = None,
    idle_timeout_s: float = 45.0,
    emit: Callable[[Observation], None] = _emit_print,
) -> str:
    """Consume the telemetry stream under the lifecycle. Returns the stop reason."""
    from google.cloud import pubsub_v1

    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT required")

    observer = TelemetryObserver()
    subscriber = pubsub_v1.SubscriberClient()
    sub_path = _ensure_subscription_seek_now(subscriber, project, topic, subscription)

    with Session(max_runtime_s=max_runtime_s, idle_timeout_s=idle_timeout_s,
                 name="telemetry") as sess:

        def callback(message: "pubsub_v1.subscriber.message.Message") -> None:
            try:
                frame = RaceFrame.model_validate_json(message.data)
            except Exception as e:
                logger.warning("bad frame, dropping: %s", e)
                message.ack()
                return
            sess.touch()                            # live frame → reset idle clock
            for obs in observer.process_frame(frame):
                emit(obs)
            message.ack()

        flow = pubsub_v1.types.FlowControl(max_messages=50)
        future = subscriber.subscribe(sub_path, callback=callback, flow_control=flow)
        logger.info("telemetry observer online — pulling %s", subscription)

        while sess.active():
            sess.wait(1.0)

        future.cancel()
        try:
            future.result(timeout=10)
        except Exception:
            pass
        subscriber.close()
    return sess.stop_reason or "stopped"


def main() -> int:
    ap = argparse.ArgumentParser(description="Telemetry Observer (deterministic stream consumer)")
    ap.add_argument("--topic", default="fe-telemetry")
    ap.add_argument("--subscription", default="fe-telemetry-observer-sub")
    ap.add_argument("--max-runtime", type=float, default=0.0,
                    help="optional deadman cap seconds; 0 = no cap (default)")
    ap.add_argument("--idle-timeout", type=float, default=45.0,
                    help="clock gate: stop after this many quiet seconds")
    ap.add_argument("--publish", action="store_true",
                    help="also publish Observations to the fe-observations bus (for the correlator)")
    args = ap.parse_args()
    emit = _emit_print
    if args.publish:
        from shared.observation_bus import make_emit
        emit = make_emit(also=_emit_print)
    reason = run(topic=args.topic, subscription=args.subscription,
                 max_runtime_s=(args.max_runtime or None), idle_timeout_s=args.idle_timeout,
                 emit=emit)
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
