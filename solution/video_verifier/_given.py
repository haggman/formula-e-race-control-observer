"""Plumbing for the Video Verifier — GIVEN, complete, and NOT part of the exercise.

You do not need to read this to build your verifier. It lives here so that
`verifier.py` — the file you actually work in — stays short enough to hold in your
head. Come back if you're curious, or when a bonus ticket sends you here.

    VideoVerdict    the four-state contract your _aggregate returns
    VerifierBase    bucket / client / camera plumbing, plus verify() and the CLI
    _parse          pull a JSON object out of a chatty model reply
    _short_error    turn an exception into one human-readable line
    LIVERIES        car number -> team livery  (used by Bonus 2)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logger = logging.getLogger("video.verifier")

DEFAULT_MODEL = "gemini-3.5-flash"
LEAD_S = 10          # seconds of context before the flagged stop
TAIL_S = 50          # seconds after — long enough for a recovering car to clear

PANEL_POS = ["TL", "TR", "BL", "BR"]

# 2024 Formula E (Berlin R10) liveries, by car number. The core verdict does NOT use
# this — it is about the TRACK, not the car's identity. Wiring it in so the model can
# name the stopped car (with a "don't invent a number you can't read" guard) is BONUS 2.
LIVERIES = {
    1:  "Andretti — white, red and blue",   17: "Andretti — white, red and blue",
    2:  "DS Penske — gold on black",         25: "DS Penske — gold on black",
    3:  "ERT",                               33: "ERT",
    4:  "Envision — green",                  16: "Envision — green",
    5:  "McLaren — papaya orange and black", 8:  "McLaren — papaya orange and black",
    7:  "Maserati — dark blue with an orange rear flash",
    18: "Maserati — dark blue with an orange rear flash",
    9:  "Jaguar — black and white",          37: "Jaguar — black and white",
    11: "ABT Cupra — copper and black",      51: "ABT Cupra — copper and black",
    13: "Porsche — white and black with red", 94: "Porsche — white and black with red",
    21: "Mahindra — matt red and silver",    48: "Mahindra — matt red and silver",
    22: "Nissan — red, white and black",     23: "Nissan — red, white and black",
}


@dataclass
class VideoVerdict:
    """The verifier's read of the track around a telemetry-flagged stop.

    `state` is one of FOUR values, and the last two are NOT interchangeable:
        "blocked" — a persistent obstruction is on the racing line
        "cleared" — a car was there but recovered / the line is clear
        "unseen"  — the check RAN and no camera saw a stopped car (a real all-clear)
        "error"   — the check could NOT run (auth / provisioning / network outage)
    An outage must never masquerade as an all-clear, so "unseen" and "error" stay distinct.
    """
    state: str
    cameras: list[str] = field(default_factory=list)   # cameras showing the blockage
    description: str = ""
    confidence: float = 0.0
    per_group: dict = field(default_factory=dict)      # raw per-group replies
    identified: Optional[int] = None                   # car number the model actually read
    error: Optional[str] = None                        # set when the check couldn't RUN

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def cleared(self) -> bool:
        return self.state == "cleared"


def _parse(text: str) -> dict:
    """Pull the JSON object out of the model's reply (tolerant of surrounding prose)."""
    s = (text or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _short_error(exc: Exception) -> str:
    """A one-line, human-friendly reason a group verify failed."""
    s = str(exc)
    up = s.upper()
    if "BEING PROVISIONED" in up or "TRY AGAIN" in up:
        return "Vertex AI service agent still provisioning — will retry"
    if "PERMISSION" in up or "403" in up or "FORBIDDEN" in up:
        return "permission denied reading the mosaics (check the Vertex service agent's storage access)"
    if "NOT FOUND" in up or "404" in up or "NO SUCH" in up:
        return "mosaic file not found (is the bucket staged?)"
    return (s[:140] + "…") if len(s) > 141 else s


class VerifierBase:
    """Bucket, client and camera plumbing. Your VideoVerifier subclasses this."""

    def __init__(self, *, bucket: Optional[str] = None, base: Optional[str] = None,
                 model: Optional[str] = None, groups: Optional[list[str]] = None):
        self.base = (base or os.environ.get("FE_MOSAICS_BASE")
                     or f"gs://{bucket or os.environ.get('MOSAICS_BUCKET')}/mosaics")
        self.model = model or os.environ.get("FE_VIDEO_MODEL") or DEFAULT_MODEL
        self._client = None
        self.groups = groups or self._list_groups()

    def _list_groups(self) -> list[str]:
        """List the mosaic group_ids in the bucket (each is <group_id>.mp4)."""
        from google.cloud import storage
        rest = self.base[len("gs://"):]
        bkt, _, prefix = rest.partition("/")
        client = storage.Client()
        out = []
        for blob in client.list_blobs(bkt, prefix=(prefix + "/") if prefix else None):
            name = os.path.basename(blob.name)
            if name.endswith(".mp4"):
                out.append(name[:-4])
        if not out:
            raise RuntimeError(f"no mosaics found under {self.base}")
        return sorted(out)

    def _ensure_client(self):
        if self._client is None:
            from shared.gemini import make_client
            self._client = make_client()
        return self._client

    def _uri(self, group_id: str) -> str:
        return f"{self.base}/{group_id}.mp4"

    @staticmethod
    def _cams(group_id: str) -> list[str]:
        """Panel cameras from the group_id (…_cam01_cam02_cam03_cam04 → Cam01…Cam04)."""
        return [p.title() for p in group_id.split("_") if p.lower().startswith("cam")]

    @staticmethod
    def _seen_car(r: dict):
        """Best-effort read of the car number the model claims it saw."""
        try:
            return int(str(r.get("seen_car")).lstrip("#"))
        except (TypeError, ValueError):
            return None

    async def verify(self, race_time_s: int, *, cars=None,
                     lead: int = LEAD_S, tail: int = TAIL_S) -> VideoVerdict:
        """The orchestration the correlator calls: sweep, then aggregate.

        `cars` = the telemetry car number(s), passed through as a hint (Bonus 2).
        Whether a blockage later CLEARS is handled by the telemetry RECOVERED signal
        (cheap + deterministic), not by re-querying video.
        """
        self._ensure_client()
        t = int(race_time_s)
        per_group, errors = await self._sweep(t, lead, tail, cars)
        return self._aggregate(per_group, errors)


def run_cli(verifier_cls) -> int:
    """The `python -m …verifier --at 693 --cars 7` entry point."""
    ap = argparse.ArgumentParser(description="One-shot CCTV verification of a telemetry stop")
    ap.add_argument("--at", type=int, required=True, help="race-second the stop was flagged")
    ap.add_argument("--bucket", default=None, help="mosaics bucket (default $MOSAICS_BUCKET)")
    ap.add_argument("--base", default=None, help="full gs:// mosaics base (overrides --bucket)")
    ap.add_argument("--lead", type=int, default=LEAD_S)
    ap.add_argument("--tail", type=int, default=TAIL_S)
    ap.add_argument("--model", default=None)
    ap.add_argument("--cars", default=None,
                    help="comma-separated car number(s) for the livery hint, e.g. 7 or 48,7")
    ap.add_argument("--out", default=None,
                    help="append the verdict (JSON line) to this file")
    args = ap.parse_args()

    cars = [int(c) for c in args.cars.split(",")] if args.cars else None
    v = verifier_cls(bucket=args.bucket, base=args.base, model=args.model)
    verdict = asyncio.run(v.verify(args.at, cars=cars, lead=args.lead, tail=args.tail))
    print(f"\nVERDICT: {verdict.state.upper()}"
          + (f"  cameras={verdict.cameras}  conf={verdict.confidence}" if verdict.blocked else "")
          + (f"  conf={verdict.confidence}" if verdict.cleared else ""))
    if verdict.description:
        print(f"  {verdict.description}")

    if args.out:
        out = os.path.expanduser(args.out)
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        rec = {"run_utc": datetime.now(timezone.utc).isoformat(), "at": args.at,
               "state": verdict.state, "cameras": verdict.cameras,
               "confidence": verdict.confidence, "description": verdict.description,
               "per_group": verdict.per_group}
        with open(out, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"  (appended to {out})")
    return 0
