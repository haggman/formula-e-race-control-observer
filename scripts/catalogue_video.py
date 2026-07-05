"""Offline VIDEO catalogue — sweep every mosaic group across the whole race.

The live video observer is clock-gated (it watches "now" ~1/sec and only while the
sim clock advances). For DISCOVERY we want the opposite: march each group's mosaic
through the model end-to-end, at a controlled cadence, and write down everything it
sees. This reuses the exact production inference (VideoObserver._observe_once →
Gemini → parse_report) but drives it from a plain race-second loop instead of the
sim clock — so there is no simulator, no Pub/Sub, no UI involved.

Output: one JSONL per group under --out (default ./catalogue), one line per
observation the model reported:
    {race_time_s, ts_utc, group_id, camera_id, signal, severity, confidence,
     car_numbers, summary}

Run in Cloud Shell (authed, MOSAICS_BUCKET set by activate.sh):
    python scripts/catalogue_video.py --all                 # all 6 groups
    python scripts/catalogue_video.py --group grp_01_cam01_cam02_cam03_cam04
    python scripts/catalogue_video.py --all --step 5 --window 10   # defaults

Cost note: --step is the seconds between calls. step=5 over a 48-min race is ~576
calls/group; sustained incidents (stops, smoke, debris) persist well beyond 5s so
they are still caught. Drop to --step 2 for finer coverage at ~2.5x the calls.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from observers.video.mosaic_source import MosaicSource                        # noqa: E402
from observers.video.observer import VideoObserver, _resolve_mosaic, GREEN_FLAG  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logger = logging.getLogger("catalogue.video")


def _base(bucket: str | None, local: str | None) -> str:
    """Base location (dir or gs:// prefix) holding the mosaics + manifest."""
    if local:
        return local
    bucket = bucket or os.environ.get("MOSAICS_BUCKET")
    if not bucket:
        raise RuntimeError("need --local DIR or MOSAICS_BUCKET / --bucket")
    return f"gs://{bucket}/mosaics"


def list_groups(bucket: str | None, local: str | None) -> list[str]:
    """Read the manifest and return every group_id (track order)."""
    base = _base(bucket, local)
    ref = os.path.join(base, "manifest.json") if local else f"{base}/manifest.json"
    import tempfile
    dest = os.path.join(tempfile.mkdtemp(prefix="manifest_"), "manifest.json")
    local_path = MosaicSource._localise(ref, dest)
    manifest = json.load(open(local_path))
    return [g["group_id"] for g in manifest.get("groups", [])]


async def sweep_group(group: str, *, bucket: str | None, local: str | None,
                      model: str | None, step: int, window: int,
                      start: int, end: int | None, out_dir: str) -> int:
    """Run one group's mosaic through the model over the race; write its JSONL."""
    mosaic_ref, manifest_ref = _resolve_mosaic(group, bucket, local)
    mosaic = MosaicSource(mosaic_ref=mosaic_ref, group_id=group,
                          manifest_ref=manifest_ref).prepare()
    last = end if end is not None else mosaic.max_second
    logger.info("group %s ready — %d frames; cataloguing %d..%d every %ds",
                group, mosaic.max_second + 1, start, last, step)

    observer = VideoObserver(clock=None, mosaic=mosaic, model=model, window_s=window)
    observer._ensure_client()

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{group}.jsonl")
    count = 0
    with open(path, "w") as fh:
        def sink(o) -> None:
            nonlocal count
            fh.write(json.dumps({
                "race_time_s": int((o.ts_utc - GREEN_FLAG).total_seconds()),
                "ts_utc": o.ts_utc.astimezone(timezone.utc).isoformat(),
                "group_id": group,
                "camera_id": o.location.camera_id,
                "signal": o.signal.value,
                "severity": o.severity_hint,
                "confidence": round(o.confidence, 3),
                "car_numbers": o.evidence.get("car_numbers", []),
                "summary": o.summary,
            }) + "\n")
            fh.flush()
            count += 1
            logger.info("  [%s] %s cam=%s sev=%s  %s",
                        o.ts_utc.strftime("%H:%M:%S"), o.signal.value,
                        o.location.camera_id, o.severity_hint, o.summary)
        observer.emit = sink

        t0 = time.monotonic()
        for s in range(start, last + 1, step):
            win_start = max(0, s - window + 1)
            try:
                await observer._observe_once(list(range(win_start, s + 1)))
            except Exception as e:
                logger.warning("  observe @%ds failed: %s", s, e)
            if s and s % (step * 20) == 0:
                logger.info("  ...%d/%ds  (%d incidents, %.0fs elapsed)",
                            s, last, count, time.monotonic() - t0)
    logger.info("group %s DONE — %d observation(s) → %s", group, count, path)
    return count


async def _amain(args) -> int:
    groups = ([args.group] if args.group
              else list_groups(args.bucket, args.local))
    if not groups:
        logger.error("no groups found (check MOSAICS_BUCKET / --local)")
        return 2
    logger.info("cataloguing %d group(s): %s", len(groups), ", ".join(groups))
    total = 0
    for g in groups:
        total += await sweep_group(
            g, bucket=args.bucket, local=args.local, model=args.model,
            step=args.step, window=args.window, start=args.start,
            end=args.end, out_dir=args.out)
    logger.info("ALL DONE — %d observation(s) across %d group(s) → %s/",
                total, len(groups), args.out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline video catalogue over full-race mosaics")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="sweep every group in the manifest")
    g.add_argument("--group", help="sweep a single group_id")
    ap.add_argument("--bucket", default=None, help="mosaics bucket (default $MOSAICS_BUCKET)")
    ap.add_argument("--local", default=None, help="local mosaics dir instead of a bucket")
    ap.add_argument("--model", default=None, help="override FE_VIDEO_MODEL")
    ap.add_argument("--step", type=int, default=5, help="seconds between model calls (default 5)")
    ap.add_argument("--window", type=int, default=10, help="frames-seconds sent per call (default 10)")
    ap.add_argument("--start", type=int, default=0, help="first race-second (default 0)")
    ap.add_argument("--end", type=int, default=None, help="last race-second (default = end of mosaic)")
    ap.add_argument("--out", default="catalogue", help="output dir (default ./catalogue)")
    args = ap.parse_args()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
