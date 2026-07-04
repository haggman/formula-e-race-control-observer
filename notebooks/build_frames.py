"""Build the 1 Hz frame stream for the simulator, from Berlin R10 telemetry.

One-time data prep (the Ch2 pattern). Downsamples the 20 Hz per-car telemetry to
one RaceFrame per race-second and writes frames.jsonl.gz — the artifact the
simulator replays. Race clock: green flag = 13:04:00 UTC (race_time_s = 0).

Each frame carries the REAL wall-clock (`ts_utc`) for that second — the anchor
that lets the video feed line up with telemetry downstream.

Usage:
  python notebooks/build_frames.py [TELEM_DIR] [OUT]
Defaults: ../_video_scratch/r10_data/telemetry  ->  simulator/frames.jsonl.gz
"""
from __future__ import annotations

import glob
import gzip
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.models import FrameCar, RaceFrame                  # noqa: E402

RACE_ID = "berlin_2024_r10"
GREEN_FLAG = pd.Timestamp("2024-05-12 13:04:00", tz="UTC")     # race_time_s = 0
RACE_END = pd.Timestamp("2024-05-12 13:52:00", tz="UTC")
STOP_SPEED_KMH = 5.0
RETIRE_HOLD_S = 60          # stopped this long => flag the car retired from here on

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TELEM = os.path.join(HERE, "..", "_video_scratch", "r10_data", "telemetry")
DEFAULT_OUT = os.path.join(HERE, "simulator", "frames.jsonl.gz")


def load_car_1hz(path: str, car: int) -> pd.DataFrame:
    """Load one car's telemetry, clip to the race, resample to 1 Hz."""
    df = pd.read_parquet(path, columns=[
        "time", "tv_speed", "tv_acc_x", "tv_acc_y", "tv_yaw_rate",
        "tv_brake", "tv_gps_lat", "tv_gps_long", "tv_gps_head", "drivername",
    ])
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = df[(df["time"] >= GREEN_FLAG) & (df["time"] <= RACE_END)].sort_values("time")
    if df.empty:
        return df
    df["sec"] = ((df["time"] - GREEN_FLAG).dt.total_seconds()).astype(int)
    # one row per race-second: last sample in that second (nearest to the tick)
    def peak_yaw(s: pd.Series) -> float:
        s = s.dropna()
        return float(s.iloc[s.abs().values.argmax()]) if len(s) else 0.0

    g = df.groupby("sec").agg(
        speed_kmh=("tv_speed", "mean"),
        accel_x=("tv_acc_x", "mean"), accel_y=("tv_acc_y", "mean"),
        yaw_rate=("tv_yaw_rate", peak_yaw),      # keep the spike, NA-safe
        brake_pct=("tv_brake", "mean"),
        lat=("tv_gps_lat", "last"), lng=("tv_gps_long", "last"),
        heading=("tv_gps_head", "last"),
        driver=("drivername", "last"),
    )
    # motion fields must be real numbers (a car can have NA seconds); GPS gaps
    # forward-fill so a car never teleports to 0,0.
    for col in ("speed_kmh", "accel_x", "accel_y", "yaw_rate", "brake_pct"):
        g[col] = g[col].fillna(0.0)
    g[["lat", "lng", "heading"]] = g[["lat", "lng", "heading"]].ffill().bfill()
    g = g.dropna(subset=["lat", "lng", "heading"])
    g["car"] = car
    return g.reset_index()


def build() -> list[RaceFrame]:
    telem_dir = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TELEM
    parts = sorted(glob.glob(os.path.join(telem_dir, "car_number=*")))
    if not parts:
        raise SystemExit(f"No telemetry under {telem_dir}")

    per_car: dict[int, pd.DataFrame] = {}
    retire_sec: dict[int, int] = {}
    for part in parts:
        car = int(part.split("car_number=")[1])
        pq = glob.glob(os.path.join(part, "*.parquet"))
        if not pq:
            continue
        d = load_car_1hz(pq[0], car)
        if d.empty:
            continue
        per_car[car] = d
        # first second of a >=RETIRE_HOLD_S stop => retired from then on
        slow = d[d["speed_kmh"] <= STOP_SPEED_KMH]
        if len(slow) >= RETIRE_HOLD_S:
            retire_sec[car] = int(slow["sec"].iloc[0])

    max_sec = max(int(d["sec"].max()) for d in per_car.values())
    # index each car's rows by second for O(1) lookup
    indexed = {car: d.set_index("sec") for car, d in per_car.items()}

    frames: list[RaceFrame] = []
    for sec in range(0, max_sec + 1):
        cars: list[FrameCar] = []
        for car, d in indexed.items():
            if sec not in d.index:
                continue
            r = d.loc[sec]
            cars.append(FrameCar(
                car_number=car,
                driver_name=(r["driver"] if isinstance(r["driver"], str) else None),
                speed_kmh=round(float(r["speed_kmh"]), 2),
                accel_x=round(float(r["accel_x"]), 3),
                accel_y=round(float(r["accel_y"]), 3),
                yaw_rate=round(float(r["yaw_rate"]), 2),
                brake_pct=round(float(r["brake_pct"]), 1),
                lat=round(float(r["lat"]), 6), lng=round(float(r["lng"]), 6),
                heading=round(float(r["heading"]), 1),
                is_retired=car in retire_sec and sec >= retire_sec[car],
            ))
        if not cars:
            continue
        frames.append(RaceFrame(
            race_id=RACE_ID, race_time_s=sec,
            ts_utc=(GREEN_FLAG + pd.Timedelta(seconds=sec)).to_pydatetime(),
            race_phase="racing", cars=cars,
        ))
    return frames


def main() -> int:
    out = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    frames = build()
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with gzip.open(out, "wt", encoding="utf-8") as f:
        for fr in frames:
            f.write(fr.model_dump_json() + "\n")
    print(f"Wrote {len(frames)} frames → {out}")
    print(f"  race_time_s 0..{frames[-1].race_time_s}  "
          f"({frames[-1].ts_utc:%H:%M:%S} UTC end)")
    print(f"  cars in mid-race frame: {len(frames[len(frames)//2].cars)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
