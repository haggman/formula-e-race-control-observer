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
RECOVER_SPEED_KMH = 150.0 # a previously-stopped car above this is genuinely RACING again.
                          # Deliberately high: a retired car under tow bounces to 50-115 km/h
                          # (recovery truck) — that is NOT racing, so tow speed must not qualify.
STILL_STOPPED_EVERY_S = 30.0  # heartbeat cadence while a confirmed stop persists
STILL_STOPPED_MAX_S = 180.0   # stop pinging after this — the car is clearly retired, not news
YAW_SETTLE_SPEED_KMH = 120.0  # a yaw/decel-disturbed car back above this is racing cleanly again
YAW_SETTLE_HOLD_S = 6.0       # …once it has held that for this long since the last disturbance
JUMP_GAP_S = 5.0         # a race-time step bigger than this (or backward) = a
                         # /jump or /restart → drop stale per-car state


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
        self._last_still: dict[int, float] = {}       # car -> last "still stopped" heartbeat epoch
        self._disturbed: dict[int, float] = {}        # car -> epoch of last yaw/decel disturbance
                                                      # (open "is it OK now?" watch until it settles)
        self._last_frame_ts: float | None = None      # for jump/restart detection

    def process_frame(self, frame: RaceFrame) -> list[Observation]:
        """Fold one frame into the per-car windows and return any new Observations."""
        out: list[Observation] = []
        now = frame.ts_utc.timestamp()

        # Time discontinuity (a /jump or /restart) → the buffers hold stale
        # samples from the old timeline; drop everything and start fresh.
        if self._last_frame_ts is not None and (
                now < self._last_frame_ts - 2 or now > self._last_frame_ts + JUMP_GAP_S):
            self._buf.clear(); self._stopped.clear(); self._stop_since.clear()
            self._escalated.clear(); self._last_fired.clear(); self._last_still.clear()
            self._disturbed.clear()
        self._last_frame_ts = now
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
                    self._disturbed.pop(car, None)          # a stop supersedes a yaw watch
                elif obs.signal in (SignalType.YAW_SPIKE, SignalType.HARD_DECEL):
                    self._disturbed[car] = now              # open/refresh the "is it OK now?" watch
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
                self._last_still[car] = now
                out.append(_prolonged_stop_obs(s, now - self._stop_since[car]))

            # Sparse "still stopped" heartbeat while the confirmed stop persists —
            # reassurance the blockage is still there, at a calm cadence and capped
            # so a retired car doesn't ping the feed forever.
            elif (self._escalated.get(car) and car in self._stop_since):
                held = now - self._stop_since[car]
                if (held <= STILL_STOPPED_MAX_S
                        and now - self._last_still.get(car, 0.0) >= STILL_STOPPED_EVERY_S):
                    self._last_still[car] = now
                    out.append(_prolonged_stop_obs(s, held))

            # A previously-stopped car back at genuine RACING speed has recovered —
            # release the latch and emit RECOVERED so the blockage clears. A RETIRED
            # car never qualifies: its later movement is the recovery truck (tow
            # speed), not the car racing, so the Safety Car must hold.
            latest = buf[-1] if buf else None
            if latest and latest.speed_kmh >= RECOVER_SPEED_KMH and not latest.is_retired:
                if self._stopped.get(car, False):
                    out.append(_recovered_obs(latest))
                self._stopped[car] = False
                self._stop_since.pop(car, None)
                self._escalated[car] = False
                self._last_still.pop(car, None)
                self._disturbed.pop(car, None)

            # Follow-up on a yaw/decel disturbance (no stop): once the car is back
            # at clean racing speed and has held it, close the loop with a "settled —
            # racing again" so a car we flagged isn't left hanging with no resolution.
            elif (car in self._disturbed and not self._stopped.get(car)
                  and latest and not latest.is_retired
                  and latest.speed_kmh >= YAW_SETTLE_SPEED_KMH
                  and now - self._disturbed[car] >= YAW_SETTLE_HOLD_S):
                out.append(_settled_obs(latest))
                self._disturbed.pop(car, None)
        return out


def _recovered_obs(s: TelemetrySample) -> Observation:
    """Build the RECOVERED Observation for a car that is racing again."""
    from shared.models import Modality, SignalType, TrackLocation
    return Observation(
        modality=Modality.TELEMETRY, signal=SignalType.RECOVERED,
        ts_utc=s.ts_utc, car_number=s.car_number, confidence=0.95, severity_hint=0,
        location=TrackLocation(gps_lat=s.lat, gps_lng=s.lng),
        summary=f"car {s.car_number} moving again at {s.speed_kmh:.0f} km/h — recovered, racing",
        evidence={"speed_kmh": round(s.speed_kmh, 1)},
    )


def _settled_obs(s: TelemetrySample) -> Observation:
    """Build the RECOVERED Observation for a yaw/decel-disturbed car that never
    stopped and is now back at clean racing speed — the 'it's fine now' follow-up."""
    from shared.models import Modality, SignalType, TrackLocation
    return Observation(
        modality=Modality.TELEMETRY, signal=SignalType.RECOVERED,
        ts_utc=s.ts_utc, car_number=s.car_number, confidence=0.9, severity_hint=0,
        location=TrackLocation(gps_lat=s.lat, gps_lng=s.lng),
        summary=f"car {s.car_number} settled — back up to racing speed ({s.speed_kmh:.0f} km/h)",
        evidence={"speed_kmh": round(s.speed_kmh, 1), "after": "disturbance"},
    )


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
    idle_timeout_s: float | None = None,
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

        from shared.heartbeat import Heartbeat
        hb = Heartbeat("telemetry", project=project)
        hb.set("online"); hb.start()

        while sess.active():
            sess.wait(1.0)

        hb.stop()
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
    ap.add_argument("--idle-timeout", type=float, default=0.0,
                    help="stop after this many quiet seconds; 0 = run until stopped (default)")
    ap.add_argument("--publish", action="store_true",
                    help="also publish Observations to the fe-observations bus (for the correlator)")
    args = ap.parse_args()
    emit = _emit_print
    if args.publish:
        from shared.observation_bus import make_emit
        emit = make_emit(also=_emit_print)
    reason = run(topic=args.topic, subscription=args.subscription,
                 max_runtime_s=(args.max_runtime or None),
                 idle_timeout_s=(args.idle_timeout or None), emit=emit)
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
