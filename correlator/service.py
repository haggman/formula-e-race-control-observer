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

# A race-clock discontinuity means the operator JUMPED or RESTARTED the sim. The
# telemetry observer already resets itself on a jump; the correlator must too, or
# the previous run's Observations linger in the 180s buffer and bleed into the next
# incident (a car from the last jump turning up chipped on this one), and
# already-announced incidents stay suppressed so a repeat run shows nothing.
JUMP_BACK_S = 2.0        # race time never runs backwards on its own
JUMP_FWD_S = 60.0        # a big forward leap = a jump (generous, so fast replay is safe)

# Green flag (race_time_s = 0) — to turn an Observation's ts_utc into a race-second
# for the video verifier (which slices the mosaic by race-second).
GREEN_FLAG = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)
# Wait this long (race-time) AFTER a telemetry stop before asking the verifier, so
# the forward window has actually played — we confirm from what happened, not by
# peeking ahead. Matches the verifier's ~50s tail plus a small margin.
VERIFY_TAIL_S = 55.0
# If the CCTV check can't RUN (e.g. a fresh-project Vertex service agent still
# provisioning), retry this many times (~a minute each) before giving up.
VERIFY_MAX_ATTEMPTS = 3

# The CCTV window the verifier will actually look at (mirrors the verifier's own
# constants) — so we can TELL the operator which footage we're reviewing.
try:
    from observers.video.verifier import LEAD_S as VIDEO_LEAD_S, TAIL_S as VIDEO_TAIL_S
except Exception:                        # verifier unavailable (telemetry-only mode)
    VIDEO_LEAD_S, VIDEO_TAIL_S = 10, 50

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
        self._last_race_s: float | None = None                # for jump/restart detection
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
        """Race-second to verify, or None if this incident doesn't warrant a CCTV
        check. Only a real STOP is worth Gemini's time — that's the persistent
        blockage video can confirm. A note-only yaw/decel (a car that twitched and
        drove on) must NOT be verified: it self-resolves in telemetry, and asking
        the model to 'confirm' a transient invites a stale/over-read of the spin
        that then contradicts the recommendation."""
        stops = [o for o in inc.observations
                 if o.modality == Modality.TELEMETRY
                 and o.signal in (SignalType.STOPPED_CAR, SignalType.PROLONGED_STOP)]
        if not stops:
            return None
        return (min(o.ts_utc for o in stops) - GREEN_FLAG).total_seconds()

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
        prev = self._verify.get(key) or {}
        win = self._window_str(stop_time)
        who = f"car #{inc.car_numbers[0]}" if inc.car_numbers else "the stopped car"

        if now is not None and now < stop_time + VERIFY_TAIL_S:
            # The footage we need hasn't PLAYED yet (we confirm from what happened,
            # never by peeking ahead). Say so immediately, so the Video Agent isn't
            # mutely idle for the ~55s it's waiting — and the operator sees why.
            if not prev.get("queued_sent"):
                prev["queued_sent"] = True
                self._verify[key] = prev
                self._publish_video_note(
                    f"[QUEUED] {who} stopped — CCTV review scheduled once the {win} "
                    f"window has played…", cars=list(inc.car_numbers), at=now)
                logger.info("CCTV review queued for stop @%ds (window %s)", int(stop_time), win)
            return

        if not prev.get("analyzing_sent"):                  # announce ONCE (survives retries)
            self._publish_video_note(
                f"[ANALYZING] reviewing CCTV {win} across all camera groups…",
                cars=list(inc.car_numbers), at=now if now is not None else stop_time)
        self._verify[key] = {"triggered": True, "verdict": None, "note": None,
                             "queued_sent": True, "analyzing_sent": True,
                             "attempts": prev.get("attempts", 0)}
        self._pool.submit(self._run_verify, key, int(stop_time), list(inc.car_numbers))
        logger.info("verifying stop @%ds on CCTV (all groups, window %s)…", int(stop_time), win)

    def _run_verify(self, key: tuple, stop_time: int, cars: list) -> None:
        try:
            verdict = asyncio.run(self.verifier.verify(stop_time, cars=cars))
        except Exception as e:
            logger.warning("verification failed @%ds: %s", stop_time, e)
            self._verify[key]["triggered"] = False          # transient — retry next tick
            return
        # A "couldn't RUN" outage (auth/provisioning) is different from a clean read:
        # give it a few attempts (~a minute each) so a fresh-project hiccup self-heals
        # without a re-jump, then surface it honestly rather than as "no CCTV view".
        if verdict.state == "error":
            attempts = self._verify[key].get("attempts", 0) + 1
            self._verify[key]["attempts"] = attempts
            if attempts < VERIFY_MAX_ATTEMPTS:
                logger.warning("video check couldn't run @%ds (attempt %d/%d): %s — retrying",
                               stop_time, attempts, VERIFY_MAX_ATTEMPTS, verdict.error)
                self._verify[key]["triggered"] = False      # retry (verdict stays None)
                return
            logger.warning("video check gave up @%ds after %d attempts: %s",
                           stop_time, attempts, verdict.error)
        self._verify[key].update(verdict=verdict.state, note=verdict.description)
        logger.info("video verdict @%ds → %s%s", stop_time, verdict.state.upper(),
                    f" ({', '.join(verdict.cameras)})" if verdict.cameras else "")
        self._publish_verification(stop_time, verdict, cars)   # → console Video Agent feed

    @staticmethod
    def _window_str(stop_time: float) -> str:
        """The CCTV window we review, as wall-clock — so the feed can say exactly
        which footage was (or will be) looked at, not just when the stop happened."""
        a = GREEN_FLAG + timedelta(seconds=stop_time - VIDEO_LEAD_S)
        b = GREEN_FLAG + timedelta(seconds=stop_time + VIDEO_TAIL_S)
        return f"{a:%H:%M:%S}–{b:%H:%M:%S}"

    def _stamp(self, at: float | None, fallback: float) -> datetime:
        """Timestamp a Video Agent line with WHEN THE AGENT SPOKE (race clock), not
        the incident time — otherwise every beat carries the stop's timestamp and
        the feed looks like it all happened at once."""
        s = at if at is not None else fallback
        return GREEN_FLAG + timedelta(seconds=float(s))

    def _publish_video_note(self, text: str, cars=None, at: float | None = None) -> None:
        """Publish a lightweight STATUS line to the Video Agent feed (queued /
        analyzing) — distinct from a verdict, and never a safety signal
        (evidence.status marks it, low severity)."""
        if self._obs_pub is None:
            logger.error("video note NOT published (no fe-observations publisher)")
            return
        try:
            self._obs_pub.publish(Observation(
                modality=Modality.VIDEO, signal=SignalType.STATIONARY_CAR_VISUAL,
                ts_utc=self._stamp(at, 0.0),
                confidence=0.3, severity_hint=5,
                car_number=(cars[0] if cars else None),
                summary=text,
                evidence={"verifier": True, "status": True, "cars": list(cars or [])}))
        except Exception as e:
            logger.warning("video note publish skipped (%s)", e)

    def _publish_verification(self, stop_time: int, verdict, cars=None) -> None:
        """Emit the verifier's read as a video Observation so the console's Video
        Agent feed shows it (one clean line per stop, not the old per-frame spam).
        The correlator ignores video obs in its own buffer, so this can't loop."""
        if self._obs_pub is None:
            logger.error("video verdict NOT published (no fe-observations publisher) — "
                         "the Video Agent column will look blank even though the check ran")
            return
        cam = verdict.cameras[0] if verdict.cameras else None
        label = {"blocked": "CONFIRMED — track blocked",
                 "cleared": "CLEARED — car recovered, line clear",
                 "unseen": "no CCTV view of this stop",
                 "error": "UNAVAILABLE — video check couldn't run"}.get(verdict.state, verdict.state)
        # chip what the VIDEO actually saw (the car it identified); only if it
        # couldn't read a number do we fall back to the telemetry-flagged car(s).
        seen = getattr(verdict, "identified", None)
        video_cars = [seen] if seen else list(cars or [])
        # State the evidence: which footage this verdict is actually based on.
        win = self._window_str(stop_time)
        detail = (verdict.description or "").strip()
        if verdict.state in ("blocked", "cleared", "unseen"):
            detail = f"{detail} · reviewed CCTV {win}".lstrip(" ·").strip()
        try:
            self._obs_pub.publish(Observation(
                modality=Modality.VIDEO, signal=SignalType.STATIONARY_CAR_VISUAL,
                ts_utc=self._stamp(self._race_now(), stop_time),
                confidence=float(verdict.confidence or 0.5),
                severity_hint=(85 if verdict.state == "blocked" else 10),
                car_number=(video_cars[0] if video_cars else None),
                location=TrackLocation(camera_id=cam),
                summary=f"[{label}] {detail}",
                evidence={"verifier": True, "verdict": verdict.state,
                          "cars": video_cars, "window": win}))
        except Exception as e:
            logger.warning("verification publish skipped (%s)", e)

    # -- fuse + announce -----------------------------------------------------
    def _check_jump(self) -> None:
        """Detect a sim jump/restart and wipe state that belongs to the old timeline."""
        now = self._race_now()
        if now is None:
            return
        last, self._last_race_s = self._last_race_s, now
        if last is None:
            return
        if now < last - JUMP_BACK_S or now > last + JUMP_FWD_S:
            logger.info("sim jumped (%.0fs → %.0fs) — clearing correlator state "
                        "(buffer, announcements, verifications)", last, now)
            self._buf.clear()
            self._announced.clear()
            self._verify.clear()

    def tick(self) -> list[IncidentReport]:
        self._check_jump()
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
            elif rank > 0 and verdict == "blocked" and prev[1] != "blocked":
                kind = "CONFIRMED"                          # only meaningful while a flag is active
            elif rank > 0 and inc.corroborated and not prev[2]:
                kind = "CONFIRMED"
            else:
                continue                                    # nothing new to say (terminal once cleared)

            if kind == "NEW" and rank == 0:
                continue                                    # a brand-new no-flag note isn't worth a card
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
                logger.error("incident publish FAILED (%s) — Race Control column won't update", e)
        else:
            logger.error("incident NOT published (no fe-incidents publisher) — the Race Control "
                         "column will look blank even though the incident was drafted")

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
        logger.info("fe-incidents publisher READY — recommendations will reach the console")
    except Exception as e:
        logger.error("!! fe-incidents publisher UNAVAILABLE (%s) — the Race Control column "
                     "will stay BLANK. Fix this before running.", e)
    try:
        svc._obs_pub = observation_bus.ObservationPublisher(project)   # verifier → Video feed
        logger.info("fe-observations publisher READY — video verdicts will reach the console")
    except Exception as e:
        logger.error("!! fe-observations publisher UNAVAILABLE (%s) — the Video Agent column "
                     "will stay BLANK. Fix this before running.", e)

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
