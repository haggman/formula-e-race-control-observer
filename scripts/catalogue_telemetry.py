"""Offline TELEMETRY catalogue — replay the whole race through the observer.

Reads the 1 Hz race frames (the same artifact the simulator publishes — each line
is a RaceFrame) and folds them, in order, through the exact production detector
(observers.telemetry.consumer.TelemetryObserver.process_frame). No Pub/Sub, no
simulator: just the deterministic detector over the full race, so the output is a
faithful list of every telemetry signal the live observer would emit.

Output JSONL (default catalogue/telemetry.jsonl), one line per signal:
    {race_time_s, ts_utc, car, signal, severity, confidence, gps, summary}

Run:
    python scripts/catalogue_telemetry.py                       # bundled frames
    python scripts/catalogue_telemetry.py --frames path/to/frames.jsonl.gz
    FRAMES_GCS=gs://.../frames.jsonl.gz python scripts/catalogue_telemetry.py
"""
from __future__ import annotations

import argparse
import gzip
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import RaceFrame                                  # noqa: E402
from observers.telemetry.consumer import TelemetryObserver           # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("catalogue.telemetry")

GREEN_FLAG = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)   # race_time_s = 0
DEFAULT_FRAMES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "simulator", "src", "frames.jsonl.gz")


def _localise_frames(path: str | None) -> str:
    gcs = os.environ.get("FRAMES_GCS")
    if path:
        return path
    if gcs:
        dest = os.path.join(tempfile.mkdtemp(prefix="frames_"), "frames.jsonl.gz")
        subprocess.run(["gcloud", "storage", "cp", gcs, dest], check=True)
        return dest
    return DEFAULT_FRAMES


def _open(path: str):
    return gzip.open(path, "rt") if path.endswith(".gz") else open(path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline telemetry catalogue over full-race frames")
    ap.add_argument("--frames", default=None, help="frames.jsonl(.gz) (default bundled sim frames)")
    ap.add_argument("--out", default="catalogue", help="output dir (default ./catalogue)")
    args = ap.parse_args()

    frames_path = _localise_frames(args.frames)
    logger.info("replaying frames: %s", frames_path)

    observer = TelemetryObserver()
    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "telemetry.jsonl")

    n_frames = count = 0
    stopped_cars: set[int] = set()
    with _open(frames_path) as fh, open(out_path, "w") as out:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                frame = RaceFrame.model_validate_json(line)
            except Exception as e:
                logger.warning("bad frame dropped: %s", e)
                continue
            n_frames += 1
            for o in observer.process_frame(frame):
                rt = int((o.ts_utc - GREEN_FLAG).total_seconds())
                gps = ([o.location.gps_lat, o.location.gps_lng]
                       if o.location and o.location.gps_lat is not None else None)
                out.write(json.dumps({
                    "race_time_s": rt,
                    "ts_utc": o.ts_utc.astimezone(timezone.utc).isoformat(),
                    "car": o.car_number, "signal": o.signal.value,
                    "severity": o.severity_hint, "confidence": round(o.confidence, 3),
                    "gps": gps, "summary": o.summary,
                }) + "\n")
                count += 1
                if o.signal.value == "stopped_car":
                    stopped_cars.add(o.car_number)
                logger.info("  t=%4ds [%s] car=%s sev=%s  %s",
                            rt, o.signal.value, o.car_number, o.severity_hint, o.summary)

    logger.info("DONE — %d frame(s), %d signal(s) → %s", n_frames, count, out_path)
    logger.info("cars with a STOPPED_CAR: %s (expected on-track [7,17,23,48]; "
                "#2 and #33 are pit stops, suppressed by the pit-lane guard)",
                sorted(stopped_cars))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
