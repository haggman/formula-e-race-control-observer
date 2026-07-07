"""Correlator service — the supervising agent (Agent 3), at runtime.

Subscribes to the observation bus (both observers publish there), keeps a rolling
buffer of recent Observations, and continuously fuses them (correlator/fusion.py).
When an incident is NEW — or ESCALATES (e.g. a telemetry-only stop that video then
corroborates, bumping double-yellow → Safety Car) — it drafts the report
(correlator/reporter.py) and writes it to Firestore `incidents/` for the Race
Control console, and prints it.

The fusion + flag policy are deterministic (cheap); the report NARRATIVE is the
only Gemini touch and it fires only on a new/escalated incident (rare), so the
correlator is cheap to run. Under the shared lifecycle: graceful stop + idle
watchdog (goes quiet when the observers do); optional deadman.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (CorrelatedIncident, FlagType, IncidentReport,     # noqa: E402
                           Modality, Observation, SignalType, TrackLocation)
from shared.lifecycle import Session                                                 # noqa: E402
from shared import observation_bus                                                    # noqa: E402
from observers.video.clock import SimClock                                           # noqa: E402
from correlator import fusion, reporter                                              # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("correlator.service")

BUFFER_S = 180.0         # keep Observations this long (race-time) for fusion —
                         # must exceed the telemetry↔video detection-latency gap
                         # (video can lag the stop by ~90s) so both survive to fuse
FUSE_EVERY_S = 2.0       # re-fuse the buffer this often

# Green flag (race_time_s = 0) — to turn an Observation's ts_utc into a race-second
# for the video verifier (which slices the mosaic by race-second).
GREEN_FLAG = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)
# Wait this long (race-time) AFTER a telemetry stop before asking the verifier, so
# the forward window has actually played — we confirm from what happened, not by
# peeking ahead. Matches the verifier's ~50s tail plus a small margin.
VERIFY_TAIL_S = 55.0

# Telemetry signals worth a video check (a stop, or a flag-worthy near-miss).
_VERIFY_TRIGGERS = {SignalType.STOPPED_CAR, SignalType.PROLONGED_STOP,
                    SignalType.HARD_DECEL, SignalType.YAW_SPIKE}

# Flag severity ordering — used to decide what counts as an ESCALATION.
_FLAG_RANK = {
    FlagType.NONE: 0, FlagType.YELLOW: 1, FlagType.DOUBLE_YELLOW: 2,
    FlagType.SAFETY_CAR: 3, FlagType.RED: 4,
}


def _incident_key(inc: CorrelatedIncident) -> tuple:
    """A stable key so the same ongoing incident isn't re-announced: the cars
    involved + a coarse time bucket. Deliberately NOT keyed on turn/location —
    those fill in as more observations (esp. video) arrive, and we want the
    telemetry-only stop and its later video corroboration to share a key so the
    second is seen as an ESCALATION, not a new incident."""
    cars = tuple(sorted(inc.car_numbers))
    bucket = int(inc.ts_utc.timestamp() // 30)
    return (cars, bucket)


class CorrelatorService:
    """Buffers Observations, fuses, and announces new/escalated incidents."""

    def __init__(self, *, use_llm: bool = True, on_report=None, race_id: str = "berlin_2024_r10",
                 sim_url: str | None = None, verifier=None):
        self.use_llm = use_llm
        self.on_report = on_report or self._default_report_sink
        self.race_id = race_id
        self._buf: deque[Observation] = deque()
        # key -> (flag rank, video verdict, corroborated)
        self._announced: dict[tuple, tuple[int, str | None, bool]] = {}
        self._db = None
        self._incident_pub = None                             # set in run() → fe-incidents
        self._obs_pub = None                                  # set in run() → fe-observations (video feed)
        # -- video verification --
        self.verifier = verifier                              # VideoVerifier or None
        self._clock = SimClock(sim_url) if sim_url else None  # to time the forward window
        self._verify: dict[tuple, dict] = {}                  # key -> {triggered, verdict, note}
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="verify")

    # -- ingest --------------------------------------------------------------
    def add(self, obs: Observation) -> None:
        self._buf.append(obs)

    def _evict(self) -> None:
        # Evict on the observations' OWN timeline (race time), not wall-clock —
        # keep the last BUFFER_S seconds relative to the newest observation.
        if not self._buf:
            return
        newest = max(o.ts_utc.timestamp() for o in self._buf)
        cutoff = newest - BUFFER_S
        self._buf = deque(o for o in self._buf if o.ts_utc.timestamp() >= cutoff)

    # -- video verification --------------------------------------------------
    def _race_now(self) -> float | None:
        if self._clock is None:
            return None
        s = self._clock.read()
        return s.race_time_s if s.reachable else None

    def _stop_time(self, inc: CorrelatedIncident) -> float | None:
        """Race-second to verify. Prefer the actual STOP (that's the blockage to
        confirm); only fall back to a hint (yaw/decel) if the incident has no stop.
        Otherwise a merged incident (e.g. a nearby yaw spike + a real stop) would
        verify the transient's moment and contradict the recommendation."""
        tele = [o for o in inc.observations if o.modality == Modality.TELEMETRY]
        stops = [o for o in tele
                 if o.signal in (SignalType.STOPPED_CAR, SignalType.PROLONGED_STOP)]
        src = stops or [o for o in tele if o.signal in _VERIFY_TRIGGERS]
        if not src:
            return None
        return (min(o.ts_utc for o in src) - GREEN_FLAG).total_seconds()

    def _maybe_verify(self, inc: CorrelatedIncident, key: tuple) -> None:
        """Attach a ready verdict; otherwise trigger the CCTV check once the
        forward window has played (so we confirm from what happened, not ahead)."""
        if self.verifier is None:
            return
        st = self._verify.get(key)
        if st and st.get("verdict") is not None:
            inc.video_verdict = st["verdict"]
            inc.video_note = st.get("note")
            return
        if st and st.get("triggered"):
            return
        stop_time = self._stop_time(inc)
        if stop_time is None:
            return
        now = self._race_now()
        if now is not None and now < stop_time + VERIFY_TAIL_S:
            return                                          # window hasn't played yet
        self._verify[key] = {"triggered": True, "verdict": None, "note": None}
        self._pool.submit(self._run_verify, key, int(stop_time), list(inc.car_numbers))
        logger.info("verifying stop @%ds on CCTV (all groups)…", int(stop_time))

    def _run_verify(self, key: tuple, stop_time: int, cars: list) -> None:
        try:
            verdict = asyncio.run(self.verifier.verify(stop_time, cars=cars))
            self._verify[key].update(verdict=verdict.state, note=verdict.description)
            logger.info("video verdict @%ds → %s%s", stop_time, verdict.state.upper(),
                        f" ({', '.join(verdict.cameras)})" if verdict.cameras else "")
            self._publish_verification(stop_time, verdict, cars)   # → console Video Agent feed
        except Exception as e:
            logger.warning("verification failed @%ds: %s", stop_time, e)
            self._verify[key]["triggered"] = False          # allow a retry next tick

    def _publish_verification(self, stop_time: int, verdict, cars=None) -> None:
        """Emit the verifier's read as a video Observation so the console's Video
        Agent feed shows it (one clean line per stop, not the old per-frame spam).
        The correlator ignores video obs in its own buffer, so this can't loop."""
        if self._obs_pub is None:
            return
        cam = verdict.cameras[0] if verdict.cameras else None
        label = {"blocked": "CONFIRMED — track blocked",
                 "cleared": "CLEARED — car recovered, line clear",
                 "unseen": "no CCTV view of this stop"}.get(verdict.state, verdict.state)
        try:
            self._obs_pub.publish(Observation(
                modality=Modality.VIDEO, signal=SignalType.STATIONARY_CAR_VISUAL,
                ts_utc=GREEN_FLAG + timedelta(seconds=stop_time),
                confidence=float(verdict.confidence or 0.5),
                severity_hint=(85 if verdict.state == "blocked" else 10),
                car_number=(cars[0] if cars else None),
                location=TrackLocation(camera_id=cam),
                summary=f"[{label}] {verdict.description}",
                evidence={"verifier": True, "verdict": verdict.state, "cars": list(cars or [])}))
        except Exception as e:
            logger.warning("verification publish skipped (%s)", e)

    # -- fuse + announce -----------------------------------------------------
    def tick(self) -> list[IncidentReport]:
        """Fuse the current buffer; return reports for any new/escalated incident."""
        self._evict()
        reports: list[IncidentReport] = []
        for inc in fusion.correlate(list(self._buf), race_id=self.race_id):
            key = _incident_key(inc)
            self._maybe_verify(inc, key)                    # attach verdict / trigger check
            flag = fusion.recommend_flag(inc)
            rank = _FLAG_RANK.get(flag.flag, 0)
            prev = self._announced.get(key)                 # (rank, verdict, corroborated) or None
            verdict = inc.video_verdict

            if prev is None:
                kind = "NEW"
            elif rank == 0 and prev[0] > 0 and prev[1] != "cleared":
                kind = "CLEARED"                            # flag dropped to none: video cleared OR car recovered
            elif rank > prev[0]:
                kind = "ESCALATION"
            elif verdict == "blocked" and prev[1] != "blocked":
                kind = "CONFIRMED"
            elif inc.corroborated and not prev[2]:
                kind = "CONFIRMED"
            else:
                continue                                    # nothing new to say
            stored_v = "cleared" if kind == "CLEARED" else (verdict or (prev[1] if prev else None))
            self._announced[key] = (max(rank, prev[0] if prev else 0), stored_v,
                                    inc.corroborated or (prev[2] if prev else False))

            report = reporter.draft_report(inc, llm=self.use_llm)
            reports.append(report)
            self.on_report(report, kind=kind)
        return reports

    # -- report sinks --------------------------------------------------------
    def _default_report_sink(self, report: IncidentReport, *, kind: str) -> None:
        corr = " [corroborated by video]" if report.incident.corroborated else ""
        print(f"\n=== {kind} INCIDENT — {report.recommendation.flag.value.upper()}{corr} ===")
        print(f"  {report.headline}")
        print(f"  {report.narrative}")
        self._write_firestore(report)
        if self._incident_pub:                 # → console (live)
            try:
                self._incident_pub.publish(kind, report)
            except Exception as e:
                logger.warning("incident publish skipped (%s)", e)

    def _write_firestore(self, report: IncidentReport) -> None:
        try:
            from google.cloud import firestore
            if self._db is None:
                self._db = firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT"))
            inc = report.incident
            doc = report.model_dump(mode="json")
            doc["updated_at_unix"] = int(time.time())
            self._db.collection("incidents").document(inc.incident_id).set(doc)
        except Exception as e:
            logger.warning("Firestore write skipped (%s)", e)


def run(*, use_llm: bool = True, max_runtime_s: float | None = None,
        idle_timeout_s: float | None = None, verify: bool = True) -> str:
    """Subscribe to the observation bus and correlate under the lifecycle."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT required")

    svc = CorrelatorService(use_llm=use_llm, sim_url=os.environ.get("SIM_URL"))
    try:
        svc._incident_pub = observation_bus.IncidentPublisher(project)
    except Exception as e:
        logger.warning("fe-incidents publisher unavailable (%s) — console live feed off", e)
    try:
        svc._obs_pub = observation_bus.ObservationPublisher(project)   # verifier → Video feed
    except Exception as e:
        logger.warning("fe-observations publisher unavailable (%s) — Video feed off", e)

    from shared.heartbeat import Heartbeat
    hb_corr = Heartbeat("correlator", project=project); hb_corr.set("online"); hb_corr.start()
    hb_video = None
    if verify:
        try:
            from observers.video.verifier import VideoVerifier
            svc.verifier = VideoVerifier()                  # reads gs:// slices on demand
            hb_video = Heartbeat("video", project=project)
            hb_video.set("online"); hb_video.start()        # no warm-up — ready immediately
            logger.info("video verifier armed (%d groups; gs:// slices, no warm-up)",
                        len(svc.verifier.groups))
        except Exception as e:
            logger.warning("video verifier unavailable (%s) — telemetry-only", e)
            if hb_video:
                hb_video.set("offline")

    with Session(max_runtime_s=max_runtime_s, idle_timeout_s=idle_timeout_s,
                 name="correlator") as sess:
        def on_obs(obs: Observation) -> None:
            sess.touch()
            # Only telemetry drives fusion. Video obs on the bus are the verifier's
            # OWN published verdicts (for the console feed) — don't re-ingest them.
            if obs.modality == Modality.TELEMETRY:
                svc.add(obs)

        subscriber, future = observation_bus.subscribe(on_obs, project=project)
        logger.info("correlator online — fusing observations")

        last = 0.0
        while sess.active():
            if time.monotonic() - last >= FUSE_EVERY_S:
                svc.tick()
                last = time.monotonic()
            sess.wait(0.5)

        future.cancel()
        try:
            future.result(timeout=10)
        except Exception:
            pass
        subscriber.close()
    hb_corr.stop()
    if hb_video:
        hb_video.stop()
    return sess.stop_reason or "stopped"


def main() -> int:
    ap = argparse.ArgumentParser(description="Correlator service (fuses the observation bus)")
    ap.add_argument("--no-llm", action="store_true", help="use the deterministic report template (no Gemini)")
    ap.add_argument("--max-runtime", type=float, default=0.0, help="optional deadman cap; 0 = none")
    ap.add_argument("--idle-timeout", type=float, default=0.0,
                    help="stop after this many quiet seconds; 0 = run until stopped (default)")
    ap.add_argument("--no-verify", action="store_true",
                    help="disable the video verifier (telemetry-only correlation)")
    args = ap.parse_args()
    reason = run(use_llm=not args.no_llm,
                 max_runtime_s=(args.max_runtime or None),
                 idle_timeout_s=(args.idle_timeout or None),
                 verify=not args.no_verify)
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
