"""Probe the telemetry detector against real Berlin R10 data.

Replays each car's 20 Hz telemetry through observers.telemetry.detector in the
same sliding-window way the streaming service will, and prints the incidents it
flags. Validates that the two known incidents fire:

  - Fenestraz #23  ~13:32:11 UTC  (retirement stop; SC at 13:32:28)
  - Gunther   #7   ~13:15:32 UTC  (stop at T1-2; SC at 13:16:12)

Usage:
  python scripts/probe_telemetry.py [PATH_TO_r10_data/telemetry]

Defaults to ../_video_scratch/r10_data/telemetry relative to the repo root.
This is a run-it, don't-edit-it harness.
"""
from __future__ import annotations

import glob
import os
import sys
from datetime import timezone

import pandas as pd

# Make the repo root importable when run as `python scripts/probe_telemetry.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import TelemetrySample                    # noqa: E402
from observers.telemetry import detector                      # noqa: E402

DEFAULT_TELEM = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "..", "_video_scratch", "r10_data", "telemetry",
)

# Detector runs on a sliding window; step it at ~1 Hz like the future service.
STEP_S = 1.0
WINDOW_S = 8.0            # a hair over STOP_HOLD_S so the stop rule has its span
DEBOUNCE_S = 10.0        # caller-owned: suppress repeats of the same (car, signal)
RACE_START = pd.Timestamp("2024-05-12 13:04:00", tz="UTC")
RACE_END = pd.Timestamp("2024-05-12 13:55:00", tz="UTC")


def load_car(path: str) -> pd.DataFrame:
    df = pd.read_parquet(path, columns=[
        "time", "tv_speed", "tv_acc_x", "tv_acc_y", "tv_yaw_rate",
        "tv_brake", "tv_gps_lat", "tv_gps_long", "tv_gps_head", "drivername",
    ])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[(df["time"] >= RACE_START) & (df["time"] <= RACE_END)]
    return df.sort_values("time").reset_index(drop=True)


def to_samples(rows: pd.DataFrame, car: int) -> list[TelemetrySample]:
    return [
        TelemetrySample(
            car_number=car,
            ts_utc=r.time.to_pydatetime().replace(tzinfo=timezone.utc),
            speed_kmh=float(r.tv_speed),
            accel_x=float(r.tv_acc_x), accel_y=float(r.tv_acc_y),
            yaw_rate=float(r.tv_yaw_rate), brake_pct=float(r.tv_brake),
            lat=float(r.tv_gps_lat), lng=float(r.tv_gps_long),
            heading=float(r.tv_gps_head),
            driver_name=(r.drivername if isinstance(r.drivername, str) else None),
        )
        for r in rows.itertuples()
    ]


def probe_car(df: pd.DataFrame, car: int) -> list[str]:
    """Slide the detector across one car's race and collect distinct incidents."""
    samples = to_samples(df, car)
    if not samples:
        return []
    ts = [s.ts_utc for s in samples]
    hits: list[str] = []
    already_stopped = False
    last_fired: dict[str, float] = {}     # signal -> last emit epoch (debounce)
    t = ts[0].timestamp()
    end = ts[-1].timestamp()
    lo = 0
    while t <= end:
        # window = samples in (t-WINDOW_S, t]
        while lo < len(ts) and ts[lo].timestamp() < t - WINDOW_S:
            lo += 1
        hi = lo
        while hi < len(ts) and ts[hi].timestamp() <= t:
            hi += 1
        window = samples[lo:hi]
        for obs in detector.detect(window, already_stopped=already_stopped):
            if obs.signal.value == "stopped_car":
                already_stopped = True
            # caller-owned debounce: one line per distinct event
            if t - last_fired.get(obs.signal.value, -1e9) < DEBOUNCE_S:
                continue
            last_fired[obs.signal.value] = t
            hits.append(
                f"  {obs.ts_utc:%H:%M:%S} [{obs.signal.value:<10}] "
                f"conf={obs.confidence:.2f} sev={obs.severity_hint:>3}  {obs.summary}"
            )
        # release the stop latch once the car is clearly moving again
        if window and window[-1].speed_kmh > 30:
            already_stopped = False
        t += STEP_S
    return hits


def main() -> int:
    telem_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TELEM
    parts = sorted(glob.glob(os.path.join(telem_dir, "car_number=*")))
    if not parts:
        print(f"No telemetry partitions under {telem_dir}", file=sys.stderr)
        return 2

    print(f"Probing {len(parts)} cars from {telem_dir}\n")
    stopped_cars = []
    for part in parts:
        car = int(part.split("car_number=")[1])
        pq = glob.glob(os.path.join(part, "*.parquet"))
        if not pq:
            continue
        df = load_car(pq[0])
        hits = probe_car(df, car)
        stops = [h for h in hits if "stopped_car" in h]
        if hits:
            drv = df["drivername"].dropna().iloc[0] if df["drivername"].notna().any() else "?"
            print(f"CAR {car} ({drv}) — {len(hits)} signal(s), {len(stops)} stop(s):")
            for h in hits[:8]:
                print(h)
            if len(hits) > 8:
                print(f"  ... +{len(hits) - 8} more")
            print()
        if stops:
            stopped_cars.append(car)

    print("=" * 60)
    print(f"Cars flagged with a STOPPED_CAR incident: {sorted(stopped_cars)}")
    print("Expected the hero incidents among them: 23 (Fenestraz), 7 (Gunther)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
