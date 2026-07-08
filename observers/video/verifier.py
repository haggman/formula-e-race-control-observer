"""VideoVerifier — telemetry-triggered, persistence-based video confirmation.

When the telemetry observer flags a stopped car, the correlator asks this to
confirm it against the CCTV. The design (validated in
notebooks/verify_camera_mapping.ipynb against the real Berlin R10 footage):

  1. Grounded + stateless. One bounded question over a short window, no rolling
     memory — so no self-reinforcing hallucination.
  2. Persistence, not presence. "By the END of the window, is the racing line
     still BLOCKED, or did the car clear/drive away?" — cleanly separates a real
     retirement (Günther/Fenestraz stay blocked) from a spin-and-recover (Evans).
  3. Track state, not car identity. The model can't reliably read a car number off
     distant CCTV, so we ask only about the track; the correlator owns the number.
  4. Sweep all groups CONCURRENTLY (asyncio.gather → ~one call of latency) and take
     the strongest blockage — our GPS→camera map proved unreliable.

VIDEO-DIRECT: rather than download mosaics and extract frames, we point Gemini
straight at each mosaic in the bucket and pass videoMetadata start/end offsets, so
it decodes ONLY the window. No download, no ffmpeg, no warm-up, no local disk. The
mosaics are 1 FPS from race-second 0, so the mp4 offset in seconds == race_time_s
(validated against the burned-in clock). We read from the project's own regional
bucket, so Vertex reads are same-region (free).

Verdict feeds the correlator's three-way fusion:
    blocked  -> corroborated -> SAFETY_CAR
    cleared  -> veto         -> no Safety Car (car recovered)
    unseen   -> telemetry-only (persistence path still escalates for blind spots)

Run standalone (after `source activate.sh`):
    python -m observers.video.verifier --at 693        # verify a race-second
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

_PANEL_POS = ["TL", "TR", "BL", "BR"]

# 2024 Formula E (Berlin R10) liveries, by car number — a hint so the verifier can
# cross-check the stopped car against the telemetry car by colour AND number.
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


# ---------------------------------------------------------------------------
# Verdict
# ---------------------------------------------------------------------------
@dataclass
class VideoVerdict:
    """The verifier's read of the track around a telemetry-flagged stop."""
    state: str                       # "blocked" | "cleared" | "unseen"
    cameras: list[str] = field(default_factory=list)   # cameras showing the blockage
    description: str = ""
    confidence: float = 0.0
    per_group: dict = field(default_factory=dict)      # raw per-group replies
    identified: Optional[int] = None                   # car number the model actually read (else None)

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def cleared(self) -> bool:
        return self.state == "cleared"


# ---------------------------------------------------------------------------
# Prompt (persistence / track-state — the notebook-validated form)
# ---------------------------------------------------------------------------
def _prompt(cams: list[str], t: int, start: int, end: int, cars=None) -> str:
    tl, tr, bl, br = (cams + ["?", "?", "?", "?"])[:4]
    hint = ""
    if cars:
        who = ", ".join(f"#{c} ({LIVERIES.get(int(c), 'livery unknown')})" for c in cars)
        hint = (f"Telemetry says the car(s) likely involved are: {who}. If you see a stopped car, "
                "use its LIVERY/colour AND its car NUMBER (if you can clearly read it) to say whether "
                "it matches. If you cannot clearly read a number, do NOT guess one — just describe the "
                "colour/livery you see.\n")
    return (
        "You are a race-control video verifier deciding whether a SAFETY CAR is warranted.\n"
        f"Telemetry flagged a car possibly stopped near here around race time ~{t}s.\n"
        + hint +
        f"This is a ~{end - start}s CCTV clip — a 2x2 mosaic of four cameras: "
        f"TL={tl}, TR={tr}, BL={bl}, BR={br} — covering that moment.\n"
        "Judge the TRACK STATE by the END of the clip (the safety call is about the track, not which "
        "car it is):\n"
        "- A car STILL stopped/stranded on or beside the racing line at the end (a persistent "
        "obstruction, maybe with marshals or a recovery vehicle): blockage=true, cleared=false.\n"
        "- A car appeared but DROVE AWAY / was recovered / the line is clear by the end: "
        "blockage=false, cleared=true.\n"
        "- No stopped car at any point: blockage=false, cleared=false.\n"
        "Note whether other cars are moving (feed live).\n"
        'Respond with a single JSON object: {"blockage": bool, "cleared": bool, '
        '"panel": "TL|TR|BL|BR|none", "feed_live": bool, '
        '"seen_car": <the stopped car\'s number if you can clearly read it, else null>, '
        '"what_you_see": str, "confidence": number}'
    )


def _parse(text: str) -> dict:
    s = (text or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            pass
    return {}


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------
class VideoVerifier:
    """Stateless, parallel, persistence-based CCTV confirmation of a telemetry stop.

    Reads each mosaic's window straight from the bucket (gs:// + videoMetadata
    offsets) — no download, no extraction, no warm-up.
    """

    def __init__(self, *, bucket: Optional[str] = None, base: Optional[str] = None,
                 model: Optional[str] = None, groups: Optional[list[str]] = None):
        self.base = (base or os.environ.get("FE_MOSAICS_BASE")
                     or f"gs://{bucket or os.environ.get('MOSAICS_BUCKET')}/mosaics")
        self.model = model or os.environ.get("FE_VIDEO_MODEL") or DEFAULT_MODEL
        self._client = None
        self.groups = groups or self._list_groups()

    # -- setup ---------------------------------------------------------------
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

    # -- one group -----------------------------------------------------------
    async def _verify_group(self, group_id: str, t: int, lead: int, tail: int, cars=None) -> dict:
        from google.genai import types
        from shared.gemini import aretry_call
        start, end = max(0, t - lead), t + tail
        cams = self._cams(group_id)
        vpart = types.Part(
            file_data=types.FileData(file_uri=self._uri(group_id), mime_type="video/mp4"),
            video_metadata=types.VideoMetadata(start_offset=f"{start}s", end_offset=f"{end}s"))
        resp = await aretry_call(lambda: self._client.aio.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user",
                                    parts=[vpart, types.Part(text=_prompt(cams, t, start, end, cars))])],
            config=types.GenerateContentConfig(temperature=0.2,
                                               response_mime_type="application/json"),
        ), what="verify")
        d = _parse(resp.text)
        d["group"] = group_id
        panel = str(d.get("panel", "none"))
        if panel in _PANEL_POS and _PANEL_POS.index(panel) < len(cams):
            d["camera"] = cams[_PANEL_POS.index(panel)]
        return d

    # -- sweep + aggregate ---------------------------------------------------
    async def _sweep(self, t: int, lead: int, tail: int, cars) -> dict:
        """One concurrent all-groups sweep at race-second t; return per-group replies."""
        results = await asyncio.gather(
            *[self._verify_group(g, t, lead, tail, cars) for g in self.groups],
            return_exceptions=True)
        per_group = {}
        for r in results:
            if isinstance(r, Exception):
                logger.warning("group verify failed: %s", r)
                continue
            per_group[r["group"]] = r
        return per_group

    @staticmethod
    def _seen_car(r: dict):
        try:
            return int(str(r.get("seen_car")).lstrip("#"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _aggregate(per_group: dict) -> VideoVerdict:
        blocked = [r for r in per_group.values() if r.get("blockage")]
        cleared = [r for r in per_group.values() if r.get("cleared")]
        if blocked:
            best = max(blocked, key=lambda r: r.get("confidence", 0) or 0)
            cams = sorted({r.get("camera") for r in blocked if r.get("camera")})
            return VideoVerdict(state="blocked", cameras=cams,
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group,
                                identified=VideoVerifier._seen_car(best))
        if cleared:
            best = max(cleared, key=lambda r: r.get("confidence", 0) or 0)
            return VideoVerdict(state="cleared",
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group,
                                identified=VideoVerifier._seen_car(best))
        return VideoVerdict(state="unseen", per_group=per_group)

    async def verify(self, race_time_s: int, *, cars=None,
                     lead: int = LEAD_S, tail: int = TAIL_S) -> VideoVerdict:
        """Sweep every camera group CONCURRENTLY; return the aggregated verdict.

        `cars` = the telemetry car number(s), used as a livery/number hint so the
        description can cross-check identity (not the safety verdict). Whether a
        blockage later CLEARS is handled by the telemetry RECOVERED signal (cheap +
        deterministic), not by re-querying video."""
        self._ensure_client()
        t = int(race_time_s)
        return self._aggregate(await self._sweep(t, lead, tail, cars))


def main() -> int:
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
                    help="append the verdict (JSON line) to this file, e.g. ~/fe_verifier_results.jsonl")
    args = ap.parse_args()

    cars = [int(c) for c in args.cars.split(",")] if args.cars else None
    v = VideoVerifier(bucket=args.bucket, base=args.base, model=args.model)
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


if __name__ == "__main__":
    raise SystemExit(main())
