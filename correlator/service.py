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
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import CorrelatedIncident, FlagType, IncidentReport, Observation  # noqa: E402
from shared.lifecycle import Session                                                 # noqa: E402
from shared import observation_bus                                                    # noqa: E402
from correlator import fusion, reporter                                              # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("correlator.service")

BUFFER_S = 180.0         # keep Observations this long (race-time) for fusion —
                         # must exceed the telemetry↔video detection-latency gap
                         # (video can lag the stop by ~90s) so both survive to fuse
FUSE_EVERY_S = 2.0       # re-fuse the buffer this often

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

    def __init__(self, *, use_llm: bool = True, on_report=None, race_id: str = "berlin_2024_r10"):
        self.use_llm = use_llm
        self.on_report = on_report or self._default_report_sink
        self.race_id = race_id
        self._buf: deque[Observation] = deque()
        self._announced: dict[tuple, tuple[int, bool]] = {}   # key -> (flag rank, corroborated)
        self._db = None

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

    # -- fuse + announce -----------------------------------------------------
    def tick(self) -> list[IncidentReport]:
        """Fuse the current buffer; return reports for any new/escalated incident."""
        self._evict()
        reports: list[IncidentReport] = []
        for inc in fusion.correlate(list(self._buf), race_id=self.race_id):
            flag = fusion.recommend_flag(inc)
            rank = _FLAG_RANK.get(flag.flag, 0)
            key = _incident_key(inc)
            prev = self._announced.get(key)                 # (rank, corroborated) or None

            escalated = prev is not None and rank > prev[0]
            newly_confirmed = inc.corroborated and (prev is None or not prev[1])
            if prev is not None and rank <= prev[0] and not newly_confirmed:
                continue                                    # nothing new to say
            self._announced[key] = (max(rank, prev[0] if prev else 0),
                                    inc.corroborated or (prev[1] if prev else False))

            kind = "NEW" if prev is None else ("ESCALATION" if escalated else "CONFIRMED")
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
        idle_timeout_s: float | None = None) -> str:
    """Subscribe to the observation bus and correlate under the lifecycle."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT required")

    svc = CorrelatorService(use_llm=use_llm)

    with Session(max_runtime_s=max_runtime_s, idle_timeout_s=idle_timeout_s,
                 name="correlator") as sess:
        def on_obs(obs: Observation) -> None:
            sess.touch()
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
    return sess.stop_reason or "stopped"


def main() -> int:
    ap = argparse.ArgumentParser(description="Correlator service (fuses the observation bus)")
    ap.add_argument("--no-llm", action="store_true", help="use the deterministic report template (no Gemini)")
    ap.add_argument("--max-runtime", type=float, default=0.0, help="optional deadman cap; 0 = none")
    ap.add_argument("--idle-timeout", type=float, default=0.0,
                    help="stop after this many quiet seconds; 0 = run until stopped (default)")
    args = ap.parse_args()
    reason = run(use_llm=not args.no_llm,
                 max_runtime_s=(args.max_runtime or None),
                 idle_timeout_s=(args.idle_timeout or None))
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
