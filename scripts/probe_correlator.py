"""End-to-end correlator probe — offline, no model calls.

Runs the deterministic telemetry detector over the real R10 stops, adds a stubbed
video Observation for the hero incident (what the Live observer WILL emit), then
fuses everything and drafts the reports. Confirms the Fenestraz #23 + Nato #17
stop fuses into ONE corroborated incident with a Safety Car recommendation.

Usage:  python scripts/probe_correlator.py [PATH_TO_r10_data/telemetry]
"""
from __future__ import annotations

import glob
import os
import sys
from datetime import datetime, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import (                                    # noqa: E402
    Modality, Observation, SignalType, TrackLocation,
)
from observers.telemetry import detector                       # noqa: E402
from correlator import fusion, reporter                        # noqa: E402
from scripts.probe_telemetry import (                          # noqa: E402
    DEFAULT_TELEM, RACE_START, RACE_END, load_car, to_samples,
)

WINDOW_S = 8.0


def telemetry_observations(telem_dir: str) -> list[Observation]:
    """Slide the detector across every car; collect one Observation per stop."""
    found: list[Observation] = []
    for part in sorted(glob.glob(os.path.join(telem_dir, "car_number=*"))):
        car = int(part.split("car_number=")[1])
        pq = glob.glob(os.path.join(part, "*.parquet"))
        if not pq:
            continue
        samples = to_samples(load_car(pq[0]), car)
        if not samples:
            continue
        ts = [s.ts_utc for s in samples]
        already = False
        t, end, lo = ts[0].timestamp(), ts[-1].timestamp(), 0
        while t <= end:
            while lo < len(ts) and ts[lo].timestamp() < t - WINDOW_S:
                lo += 1
            hi = lo
            while hi < len(ts) and ts[hi].timestamp() <= t:
                hi += 1
            for o in detector.detect(samples[lo:hi], already_stopped=already):
                if o.signal == SignalType.STOPPED_CAR:
                    already = True
                    found.append(o)          # keep the stop observations
            if samples[lo:hi] and samples[hi - 1].speed_kmh > 30:
                already = False
            t += 1.0
    return found


def stub_video_observation() -> Observation:
    """What the Live video observer will emit for the hero incident (~13:32:12)."""
    return Observation(
        modality=Modality.VIDEO,
        signal=SignalType.STATIONARY_CAR_VISUAL,
        ts_utc=datetime(2024, 5, 12, 13, 32, 12, tzinfo=timezone.utc),
        car_number=23,
        confidence=0.82,
        severity_hint=75,
        location=TrackLocation(camera_id="cctv_T15", turn="T15"),
        summary="two cars stopped against the barrier, marshals approaching",
        evidence={"car_numbers": [23, 17]},
    )


def main() -> int:
    telem_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TELEM
    tel_obs = telemetry_observations(telem_dir)
    print(f"Telemetry stop observations: {len(tel_obs)} "
          f"(cars {sorted({o.car_number for o in tel_obs})})")

    all_obs = tel_obs + [stub_video_observation()]
    incidents = fusion.correlate(all_obs)
    print(f"Fused into {len(incidents)} incident(s).\n")

    for inc in incidents:
        rep = reporter.draft_report(inc)      # template narrative (offline)
        print(rep.headline)
        print(f"  when: {inc.ts_utc:%H:%M:%S} UTC   cars: {inc.car_numbers}   "
              f"corroborated: {inc.corroborated}   modalities: "
              f"{sorted({o.modality.value for o in inc.observations})}")
        print(f"  FLAG: {rep.recommendation.flag.value.upper()} "
              f"{rep.recommendation.turns} — {rep.recommendation.rationale}")
        print(f"  report: {rep.narrative}\n")

    # Explicit check on the hero incident
    hero = next((i for i in incidents if 23 in i.car_numbers and 17 in i.car_numbers), None)
    ok = bool(hero and hero.corroborated
              and reporter.draft_report(hero).recommendation.flag.value == "safety_car")
    print("=" * 60)
    print(f"HERO INCIDENT (#23 + #17, corroborated, Safety Car): "
          f"{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
