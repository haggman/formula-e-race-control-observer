"""VideoVerifier — telemetry-triggered, persistence-based video confirmation.

*** STARTER — THIS IS THE FILE YOU BUILD. ***
Three things are stubbed and raise NotImplementedError until you write them:
    _prompt(...)                 Task 2 — the persistence question + JSON contract
    VideoVerifier._verify_group  Task 3 — one Gemini call over one mosaic slice
    VideoVerifier._aggregate     Task 3 — fuse six replies into ONE verdict
Everything else is given and working. The correlator already calls verify() at the
right moment with the right arguments; your job is everything behind that door.

Stuck? The complete reference is solution/video_verifier/verifier.py — open the same
method there. Using it is shipping, not cheating.

Test it standalone (after `source activate.sh`), no full stack required:
    python -m starter.video_verifier.verifier --at 693 --cars 7

Orientation (read HOW_IT_WORKS.md for the long version):
  1. Grounded + stateless. One bounded question over a short window — no rolling
     memory, so no self-reinforcing hallucination.
  2. Persistence, not presence. "By the END of the window, is the racing line
     still BLOCKED, or did the car clear/drive away?" — that one framing separates
     a real retirement from a spin-and-recover.
  3. Track state, not car identity. The model can't reliably read a car number off
     distant CCTV, so ask only about the track; the correlator owns the number.

VIDEO-DIRECT: rather than download mosaics and extract frames, you point Gemini
straight at each mosaic in the bucket and pass videoMetadata start/end offsets, so
it decodes ONLY the window. No download, no ffmpeg, no warm-up, no local disk. The
mosaics are 1 FPS from race-second 0, so the mp4 offset in seconds == race_time_s
(you prove this in the notebook). Same-region bucket reads are free.

Your verdict feeds the correlator's three-way fusion:
    blocked  -> corroborated -> SAFETY_CAR
    cleared  -> veto         -> no Safety Car (car recovered)
    unseen   -> telemetry-only (no camera saw it)
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

# 2024 Formula E (Berlin R10) liveries, by car number. GIVEN, but the core prompt
# does NOT use this — the core verdict is about the TRACK, not the car's identity.
# Wiring it in so the model can name the stopped car (with a "don't invent a number
# you can't read" guard) is BONUS 2.
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
# Verdict  (GIVEN — this is the contract; do not change it)
# ---------------------------------------------------------------------------
@dataclass
class VideoVerdict:
    """The verifier's read of the track around a telemetry-flagged stop.

    state is one of FOUR values, and the last two are NOT interchangeable:
        "blocked" — a persistent obstruction is on the racing line
        "cleared" — a car was there but recovered / the line is clear
        "unseen"  — the check RAN and no camera saw a stopped car (a real all-clear)
        "error"   — the check could NOT run (auth / provisioning / network outage)
    An outage must never masquerade as an all-clear, so "unseen" and "error" are
    kept distinct.
    """
    state: str
    cameras: list[str] = field(default_factory=list)   # cameras showing the blockage
    description: str = ""
    confidence: float = 0.0
    per_group: dict = field(default_factory=dict)      # raw per-group replies
    identified: Optional[int] = None                   # car number the model actually read (else None)
    error: Optional[str] = None                        # set when the check couldn't RUN

    @property
    def blocked(self) -> bool:
        return self.state == "blocked"

    @property
    def cleared(self) -> bool:
        return self.state == "cleared"


# ---------------------------------------------------------------------------
# Prompt  — TASK 3: paste the _prompt you built in the notebook (Task 2) here.
# ---------------------------------------------------------------------------
def _prompt(cams: list[str], t: int, start: int, end: int, cars=None) -> str:
    """Build the text prompt for ONE 2x2 mosaic clip.

    You wrote and tuned this in the notebook (Task 2); Task 3 is to drop it in. This
    function has the SAME signature and the SAME given `context`/`json_contract` tail
    as the notebook cell, so you can paste your notebook `_prompt` straight over this
    one — or just paste your `logic`. `cams`, `t`, `start`, `end`, `cars` are the same
    variables you had in the notebook.
    """
    tl, tr, bl, br = (cams + ["?", "?", "?", "?"])[:4]

    # ===== YOUR LOGIC (from Task 2) — the question + how to BRIDGE what Gemini sees =====
    #   when is blockage true vs cleared?  -> judge the END of the window
    #   which panel (TL/TR/BL/BR) is it in? -> panel     (code maps panel -> camera)
    #   the car's number if legible, else null -> seen_car
    #   a one-line description -> what_you_see ; other cars moving -> feed_live ; confidence
    #   (`cars` + the module-level LIVERIES table are Bonus 2 — naming the car.)
    logic = """
    << paste the question you built in the notebook here >>
    """

    # ===== GIVEN — clip context + the JSON contract the code depends on (don't change) =
    context = (f"This is a ~{end - start}s CCTV clip — a 2x2 mosaic of four cameras: "
               f"TL={tl}, TR={tr}, BL={bl}, BR={br}.")
    json_contract = ('Respond with a SINGLE JSON object: {"blockage": bool, "cleared": bool, '
                     '"panel": "TL|TR|BL|BR|none", "feed_live": bool, '
                     '"seen_car": <car number if clearly readable, else null>, '
                     '"what_you_see": string, "confidence": number}')

    if "<<" in logic:
        raise NotImplementedError(
            "TASK 3: paste the `_prompt` (or its `logic`) you built in "
            "notebooks/fe_video_lab.ipynb (Task 2) into `logic` above. See STUDENT_GUIDE.md Task 3.")
    return f"{logic}\n{context}\n{json_contract}"


def _parse(text: str) -> dict:
    """GIVEN — pull the JSON object out of the model's reply (tolerant of prose)."""
    s = (text or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        try:
            return json.loads(s[a:b + 1])
        except json.JSONDecodeError:
            pass
    return {}


def _short_error(exc: Exception) -> str:
    """GIVEN — a one-line, human-friendly reason a group verify failed."""
    s = str(exc)
    up = s.upper()
    if "BEING PROVISIONED" in up or "TRY AGAIN" in up:
        return "Vertex AI service agent still provisioning — will retry"
    if "PERMISSION" in up or "403" in up or "FORBIDDEN" in up:
        return "permission denied reading the mosaics (check the Vertex service agent's storage access)"
    if "NOT FOUND" in up or "404" in up or "NO SUCH" in up:
        return "mosaic file not found (is the bucket staged?)"
    return (s[:140] + "…") if len(s) > 141 else s


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------
class VideoVerifier:
    """Stateless, persistence-based CCTV confirmation of a telemetry stop.

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

    # -- setup (GIVEN) -------------------------------------------------------
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

    # -- one group — TASK 3a: write this ------------------------------------
    async def _verify_group(self, group_id: str, t: int, lead: int, tail: int, cars=None) -> dict:
        """Ask Gemini about ONE camera group's window. THIS IS TASK 3 — write it.

        The novel move: point Gemini straight at the mosaic in the bucket and pass
        videoMetadata offsets so it decodes ONLY [t-lead, t+tail] — no download.

        You will need, roughly:
            from google.genai import types
            from shared.gemini import aretry_call
            start, end = max(0, t - lead), t + tail
            cams = self._cams(group_id)
            # a VIDEO part pointing at self._uri(group_id), sliced by offsets:
            #   types.Part(
            #       file_data=types.FileData(file_uri=..., mime_type="video/mp4"),
            #       video_metadata=types.VideoMetadata(start_offset=f"{start}s",
            #                                           end_offset=f"{end}s"))
            # + a TEXT part: types.Part(text=_prompt(cams, t, start, end, cars))
            # call: await self._client.aio.models.generate_content(
            #           model=self.model, contents=[Content(role="user", parts=[...])],
            #           config=types.GenerateContentConfig(
            #               temperature=0.2, response_mime_type="application/json"))
            # wrap the call in aretry_call(lambda: ..., what="verify")

        Then turn the reply into a dict the aggregator can read:
            d = _parse(resp.text)
            d["group"] = group_id
            panel = str(d.get("panel", "none"))
            if panel in _PANEL_POS and _PANEL_POS.index(panel) < len(cams):
                d["camera"] = cams[_PANEL_POS.index(panel)]   # panel -> real camera id
            return d
        """
        raise NotImplementedError(
            "TASK 3: make one Gemini call over the gs:// mosaic slice and return the "
            "parsed dict (with 'group' and, if a panel was named, 'camera'). "
            "Prototype it in the notebook first — see STUDENT_GUIDE.md Task 3."
        )

    # -- sweep (GIVEN — but SEQUENTIAL on purpose; see Bonus 1) --------------
    async def _sweep(self, t: int, lead: int, tail: int, cars):
        """Sweep every camera group at race-second t; return (per_group, errors).

        GIVEN — but note it runs the six groups ONE AT A TIME, in a plain for-loop.
        It works, but it's SLOW: six back-to-back ~10s Gemini calls ≈ ~60s per stop.
        These calls are independent and I/O-bound — nothing about them needs to be
        serial. BONUS 1 is to make this sweep concurrent (asyncio.gather) and watch
        ~60s collapse to ~10s. Leave it sequential for now; get a correct verdict
        first, then make it fast.

        `errors` lets the caller tell a real 'saw nothing' from a check that never
        RAN (auth / provisioning / network) — keep appending _short_error(e) on
        failures so _aggregate can surface an outage honestly.
        """
        per_group, errors = {}, []
        for g in self.groups:                       # <-- Bonus 1: fan these out concurrently
            try:
                r = await self._verify_group(g, t, lead, tail, cars)
            except Exception as e:                  # a failing group must not sink the sweep
                logger.warning("group verify failed: %s", e)
                errors.append(_short_error(e))
                continue
            per_group[r["group"]] = r
        return per_group, errors

    @staticmethod
    def _seen_car(r: dict):
        """GIVEN — best-effort read of the car number the model claims it saw."""
        try:
            return int(str(r.get("seen_car")).lstrip("#"))
        except (TypeError, ValueError):
            return None

    # -- aggregate — TASK 3b: write this ------------------------------------
    @staticmethod
    def _aggregate(per_group: dict, errors: list | None = None) -> VideoVerdict:
        """Fuse the per-group replies into ONE VideoVerdict. THIS IS TASK 3 — write it.

        `per_group` maps group_id -> the dict your _verify_group returned. Decide the
        single verdict for the whole incident.

        CORE (enough to pass the acceptance tests, which are all blockages):
          * If ANY group reports a blockage -> state="blocked". Collect the cameras
            that saw it, take the most-confident reply for the description/confidence,
            and (bonus) the car number it read via VideoVerifier._seen_car(best).
          * Otherwise -> state="unseen" (no camera saw a stopped car).

        BONUS — the two states that make the verifier honest (see BONUS.md):
          * BONUS 4 (the veto): if no blockage but a group reports 'cleared' ->
            state="cleared" (the car recovered / the line is clear). fusion stands
            the flag down on this.
          * BONUS 5 (honest errors): if NO group ran at all (per_group empty AND
            errors non-empty) -> state="error", description=errors[0], error=errors[0].
            An outage must NOT masquerade as a clean 'unseen' all-clear.
        """
        raise NotImplementedError(
            "TASK 3: fuse the per-group replies into ONE VideoVerdict. Honour all "
            "four states — especially the difference between 'unseen' (ran, saw "
            "nothing) and 'error' (never ran). See STUDENT_GUIDE.md Task 3."
        )

    async def verify(self, race_time_s: int, *, cars=None,
                     lead: int = LEAD_S, tail: int = TAIL_S) -> VideoVerdict:
        """GIVEN — the orchestration the correlator calls. Sweep, then aggregate.

        `cars` = the telemetry car number(s), passed through as a hint (Bonus 2).
        Whether a blockage later CLEARS is handled by the telemetry RECOVERED signal
        (cheap + deterministic), not by re-querying video."""
        self._ensure_client()
        t = int(race_time_s)
        per_group, errors = await self._sweep(t, lead, tail, cars)
        return self._aggregate(per_group, errors)


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
