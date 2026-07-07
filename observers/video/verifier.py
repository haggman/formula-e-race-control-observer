"""VideoVerifier — telemetry-triggered, persistence-based video confirmation.

This REPLACES the streaming video observer. It is NOT a detector that watches the
race and reports incidents (that approach hallucinated persistent pileups — see
notebooks/verify_camera_mapping.ipynb). Instead, when the *telemetry* observer
flags a stopped car, the correlator asks THIS to confirm it against the CCTV.

The design, validated in the notebook against the real Berlin R10 footage:

  1. Grounded + stateless. One bounded question over a short window, no rolling
     memory — so no self-reinforcing hallucination.
  2. Persistence, not presence. The question is "by the END of the window, is the
     racing line still BLOCKED, or did the car clear/drive away?" That is what
     cleanly separates a real retirement (Günther/Fenestraz stay blocked) from a
     spin-and-recover (Evans clears) — where "is a car visible" over-confirmed.
  3. Track state, not car identity. The model can't reliably read a car number off
     distant CCTV (it just echoes what you tell it), so we ask only about the track
     and let the correlator own the car number from telemetry.
  4. Sweep all groups CONCURRENTLY. Our GPS→camera map proved unreliable, so rather
     than trust it we ask every camera group at once (asyncio.gather → ~one call of
     latency) and take the strongest blockage.

Verdict feeds the correlator's three-way fusion:
    blocked  -> corroborated -> SAFETY_CAR
    cleared  -> veto         -> no Safety Car (car recovered)
    unseen   -> telemetry-only (persistence path still escalates for blind spots)

Run standalone (after `source activate.sh`, mosaics staged):
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

from observers.video.mosaic_source import MosaicSource                     # noqa: E402
from observers.video.observer import _resolve_mosaic                       # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("google_genai.models").setLevel(logging.WARNING)
logger = logging.getLogger("video.verifier")

DEFAULT_MODEL = "gemini-3.5-flash"
LEAD_S = 10          # seconds of context before the flagged stop
TAIL_S = 50          # seconds after — long enough for a recovering car to clear
STEP_S = 4           # sample cadence (≈15 frames over the window)

_PANEL_POS = ["TL", "TR", "BL", "BR"]


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

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def cleared(self) -> bool:
        return self.state == "cleared"


# ---------------------------------------------------------------------------
# Prompt (persistence / track-state — the notebook-validated form)
# ---------------------------------------------------------------------------
def _prompt(cams: list[str], t: int, n_frames: int, step: int) -> str:
    tl, tr, bl, br = (cams + ["?", "?", "?", "?"])[:4]
    return (
        "You are a race-control video verifier deciding whether a SAFETY CAR is warranted.\n"
        f"Telemetry flagged a car possibly stopped near here around race time ~{t}s.\n"
        f"These {n_frames} frames are in time order, 1 every {step}s, from a 2x2 CCTV mosaic: "
        f"TL={tl}, TR={tr}, BL={bl}, BR={br}.\n"
        "Watch the SEQUENCE and judge the state by the LAST frames:\n"
        "- If a car is STILL stopped/stranded on or beside the racing line at the end (a persistent "
        "obstruction, perhaps with marshals or a recovery vehicle): blockage=true, cleared=false.\n"
        "- If a car appeared but DROVE AWAY / was recovered / the line is clear by the end: "
        "blockage=false, cleared=true.\n"
        "- If no stopped car at any point: blockage=false, cleared=false.\n"
        "Do NOT identify the car number; judge only the track state. Note whether other cars are "
        "moving (feed live).\n"
        'Respond with a single JSON object: {"blockage": bool, "cleared": bool, '
        '"panel": "TL|TR|BL|BR|none", "feed_live": bool, "what_you_see": str, "confidence": number}'
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
    """Stateless, parallel, persistence-based CCTV confirmation of a telemetry stop."""

    def __init__(self, *, bucket: Optional[str] = None, local: Optional[str] = None,
                 model: Optional[str] = None, groups: Optional[list[str]] = None):
        self.bucket = bucket
        self.local = local
        self.model = model or os.environ.get("FE_VIDEO_MODEL") or DEFAULT_MODEL
        self._client = None
        self._mosaics: dict[str, MosaicSource] = {}   # group_id -> prepared source
        self.groups = groups or self._list_groups()

    # -- setup ---------------------------------------------------------------
    def _list_groups(self) -> list[str]:
        """Read the manifest and return every group_id (track order)."""
        base = (self.local if self.local
                else f"gs://{self.bucket or os.environ.get('MOSAICS_BUCKET')}/mosaics")
        ref = os.path.join(base, "manifest.json") if self.local else f"{base}/manifest.json"
        import tempfile
        dest = os.path.join(tempfile.mkdtemp(prefix="manifest_"), "manifest.json")
        manifest = json.load(open(MosaicSource._localise(ref, dest)))
        return [g["group_id"] for g in manifest.get("groups", [])]

    def _ensure_client(self):
        if self._client is None:
            from shared.gemini import make_client
            self._client = make_client()
        return self._client

    def _mosaic(self, group_id: str) -> MosaicSource:
        """Lazily download + extract a group's mosaic (cached for the session)."""
        ms = self._mosaics.get(group_id)
        if ms is None:
            mosaic_ref, manifest_ref = _resolve_mosaic(group_id, self.bucket, self.local)
            ms = MosaicSource(mosaic_ref=mosaic_ref, group_id=group_id,
                              manifest_ref=manifest_ref).prepare()
            self._mosaics[group_id] = ms
            logger.info("prepared mosaic %s (%d frames)", group_id, ms.max_second + 1)
        return ms

    def warmup(self, on_progress=None) -> None:
        """Download + extract every group's mosaic up front, so verifications never
        pull anything at request time. `on_progress(done, total)` is called after
        each group (used to surface warm-up progress in the console)."""
        n = len(self.groups)
        logger.info("warming %d mosaics locally…", n)
        for i, g in enumerate(self.groups, 1):
            self._mosaic(g)
            logger.info("  mosaic %d/%d ready", i, n)
            if on_progress:
                on_progress(i, n)
        logger.info("✅ all %d mosaics local — verifier ready, no downloads at request time", n)

    def _cams(self, ms: MosaicSource, group_id: str) -> list[str]:
        if ms.panels:
            return [p.get("camera_id", "?") for p in ms.panels]
        # fall back to the ids embedded in the group_id (…_cam01_cam02_…)
        return [p.title() for p in group_id.split("_") if p.lower().startswith("cam")]

    # -- one group -----------------------------------------------------------
    async def _verify_group(self, group_id: str, t: int, lead: int, tail: int, step: int) -> dict:
        from google.genai import types
        from shared.gemini import aretry_call
        ms = self._mosaic(group_id)
        secs = range(max(0, t - lead), t + tail + 1, step)
        frames = [fp for s in secs if (fp := ms.frame_path(s))]
        if not frames:
            return {"group": group_id, "blockage": False, "cleared": False}
        cams = self._cams(ms, group_id)
        parts = [types.Part.from_bytes(data=open(f, "rb").read(), mime_type="image/jpeg")
                 for f in frames]
        parts.append(types.Part(text=_prompt(cams, t, len(frames), step)))
        resp = await aretry_call(lambda: self._client.aio.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(temperature=0.2,
                                               response_mime_type="application/json"),
        ), what="verify")
        d = _parse(resp.text)
        d["group"] = group_id
        # resolve the named panel to a camera_id
        panel = str(d.get("panel", "none"))
        if panel in _PANEL_POS and _PANEL_POS.index(panel) < len(cams):
            d["camera"] = cams[_PANEL_POS.index(panel)]
        return d

    # -- sweep + aggregate ---------------------------------------------------
    async def verify(self, race_time_s: int, *, lead: int = LEAD_S, tail: int = TAIL_S,
                     step: int = STEP_S) -> VideoVerdict:
        """Sweep every camera group CONCURRENTLY; return the aggregated verdict."""
        self._ensure_client()
        t = int(race_time_s)
        results = await asyncio.gather(
            *[self._verify_group(g, t, lead, tail, step) for g in self.groups],
            return_exceptions=True,
        )
        per_group, blocked, cleared = {}, [], []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("group verify failed: %s", r)
                continue
            per_group[r["group"]] = r
            if r.get("blockage"):
                blocked.append(r)
            elif r.get("cleared"):
                cleared.append(r)

        if blocked:
            best = max(blocked, key=lambda r: r.get("confidence", 0) or 0)
            cams = sorted({r.get("camera") for r in blocked if r.get("camera")})
            return VideoVerdict(state="blocked", cameras=cams,
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group)
        if cleared:
            best = max(cleared, key=lambda r: r.get("confidence", 0) or 0)
            return VideoVerdict(state="cleared",
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group)
        return VideoVerdict(state="unseen", per_group=per_group)


def main() -> int:
    ap = argparse.ArgumentParser(description="One-shot CCTV verification of a telemetry stop")
    ap.add_argument("--at", type=int, required=True, help="race-second the stop was flagged")
    ap.add_argument("--bucket", default=None, help="mosaics bucket (default $MOSAICS_BUCKET)")
    ap.add_argument("--local", default=None, help="local mosaics dir instead of a bucket")
    ap.add_argument("--lead", type=int, default=LEAD_S)
    ap.add_argument("--tail", type=int, default=TAIL_S)
    ap.add_argument("--step", type=int, default=STEP_S)
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None,
                    help="append the verdict (JSON line) to this file so it survives a "
                         "Cloud Shell restart, e.g. --out ~/fe_verifier_results.jsonl")
    args = ap.parse_args()

    v = VideoVerifier(bucket=args.bucket, local=args.local, model=args.model)
    verdict = asyncio.run(v.verify(args.at, lead=args.lead, tail=args.tail, step=args.step))
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
