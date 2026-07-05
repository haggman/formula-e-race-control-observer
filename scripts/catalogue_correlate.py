"""Offline CORRELATE — merge the video + telemetry catalogues into one timeline.

Loads every observation written by catalogue_video.py (per-group JSONL) and
catalogue_telemetry.py (telemetry.jsonl), rebuilds them as Observations, and runs
the SAME deterministic fusion the live correlator uses (correlator.fusion) to get
candidate incidents + the flag each would recommend. This is the sheet we read
together to choose the demo jump buttons.

Output:
  - catalogue/incidents.jsonl — one line per correlated incident
  - a printed table, sorted by race time, marking corroborated (telemetry+video)
    incidents with ★.

Run (after the two catalogues exist under ./catalogue):
    python scripts/catalogue_correlate.py
    python scripts/catalogue_correlate.py --dir catalogue --window 120
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.models import Modality, Observation, SignalType, TrackLocation   # noqa: E402
from correlator import fusion                                                # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("catalogue.correlate")

GREEN_FLAG = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)


def _obs_from_video(d: dict) -> Observation | None:
    try:
        return Observation(
            modality=Modality.VIDEO, signal=SignalType(d["signal"]),
            ts_utc=datetime.fromisoformat(d["ts_utc"]),
            car_number=(d.get("car_numbers") or [None])[0],
            confidence=float(d.get("confidence", 0.5)),
            severity_hint=int(d.get("severity", 0)),
            location=TrackLocation(camera_id=d.get("camera_id")),
            summary=d.get("summary", ""),
            evidence={"car_numbers": d.get("car_numbers", [])},
        )
    except Exception:
        return None


def _obs_from_telemetry(d: dict) -> Observation | None:
    try:
        gps = d.get("gps") or [None, None]
        return Observation(
            modality=Modality.TELEMETRY, signal=SignalType(d["signal"]),
            ts_utc=datetime.fromisoformat(d["ts_utc"]),
            car_number=d.get("car"),
            confidence=float(d.get("confidence", 0.9)),
            severity_hint=int(d.get("severity", 0)),
            location=TrackLocation(gps_lat=gps[0], gps_lng=gps[1]),
            summary=d.get("summary", ""),
        )
    except Exception:
        return None


def load_observations(cat_dir: str) -> list[Observation]:
    obs: list[Observation] = []
    tele = os.path.join(cat_dir, "telemetry.jsonl")
    if os.path.exists(tele):
        for line in open(tele):
            if line.strip():
                o = _obs_from_telemetry(json.loads(line))
                if o:
                    obs.append(o)
    for path in sorted(glob.glob(os.path.join(cat_dir, "grp_*.jsonl"))):
        for line in open(path):
            if line.strip():
                o = _obs_from_video(json.loads(line))
                if o:
                    obs.append(o)
    return obs


def main() -> int:
    ap = argparse.ArgumentParser(description="Correlate the video + telemetry catalogues")
    ap.add_argument("--dir", default="catalogue", help="catalogue dir (default ./catalogue)")
    ap.add_argument("--window", type=float, default=fusion.CORRELATION_WINDOW_S,
                    help=f"correlation window seconds (default {fusion.CORRELATION_WINDOW_S:.0f})")
    args = ap.parse_args()

    obs = load_observations(args.dir)
    if not obs:
        logger.error("no observations found under %s (run the catalogue scripts first)", args.dir)
        return 2
    logger.info("loaded %d observation(s) — %d telemetry, %d video\n",
                len(obs), sum(o.modality == Modality.TELEMETRY for o in obs),
                sum(o.modality == Modality.VIDEO for o in obs))

    incidents = fusion.correlate(obs, window_s=args.window)
    out_path = os.path.join(args.dir, "incidents.jsonl")
    rows = []
    with open(out_path, "w") as fh:
        for inc in incidents:
            flag = fusion.recommend_flag(inc)
            rt = int((inc.ts_utc - GREEN_FLAG).total_seconds())
            mods = sorted({o.modality.value for o in inc.observations})
            cams = sorted({o.location.camera_id for o in inc.observations
                           if o.location and o.location.camera_id})
            row = {
                "race_time_s": rt,
                "ts_utc": inc.ts_utc.astimezone(timezone.utc).isoformat(),
                "cars": inc.car_numbers, "corroborated": inc.corroborated,
                "modalities": mods, "cameras": cams,
                "severity": inc.severity, "flag": flag.flag.value,
                "n_obs": len(inc.observations),
                "signals": sorted({o.signal.value for o in inc.observations}),
            }
            fh.write(json.dumps(row) + "\n")
            rows.append(row)

    # readable table, sorted by race time
    logger.info("%-8s %-4s %-13s %-10s %-18s %s", "t(s)", "★", "flag", "cars", "modalities", "signals / cameras")
    logger.info("-" * 96)
    for r in sorted(rows, key=lambda x: x["race_time_s"]):
        star = "★" if r["corroborated"] else " "
        who = ",".join("#" + str(c) for c in r["cars"]) or "-"
        detail = ",".join(r["signals"])
        if r["cameras"]:
            detail += "  [" + ",".join(r["cameras"]) + "]"
        logger.info("%-8d  %s   %-13s %-10s %-18s %s",
                    r["race_time_s"], star, r["flag"], who, "+".join(r["modalities"]), detail)
    logger.info("\n%d candidate incident(s) → %s", len(rows), out_path)
    logger.info("★ = corroborated (telemetry + video agree) — the strongest demo material")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
