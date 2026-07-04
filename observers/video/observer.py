"""Video Observer — Gemini Live API at 1 FPS (Observer 1).

Streams a CCTV clip into a Gemini Live session one frame per second and, on a
fixed cadence, asks the model for a structured incident report. Each positive
report becomes a video `Observation` carrying an absolute UTC timestamp, so the
correlator can fuse it with the telemetry stop.

RUN THE INFERENCE IN CLOUD SHELL / a GCP project. The live call needs Vertex or
Gemini-API credentials. This module also has a `--dry-run` mode that does the
whole pipeline EXCEPT the model call (extract frames, map timestamps, assemble
the exact prompts) so the plumbing can be validated cheaply, offline.

Auth (Vertex, recommended for the hack):
    export GOOGLE_GENAI_USE_VERTEXAI=true
    export GOOGLE_CLOUD_PROJECT=<your-qwiklabs-project>
    export GOOGLE_CLOUD_LOCATION=us-central1

Usage:
    python -m observers.video.observer CLIP.mp4 --start 2024-05-12T13:32:00Z
    python -m observers.video.observer CLIP.mp4 --start ... --dry-run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.models import (                                    # noqa: E402
    Modality, Observation, SignalType, TrackLocation,
)
from observers.video import prompts                            # noqa: E402
from observers.video.frame_source import Frame, extract_frames  # noqa: E402

# Vertex uses the enterprise live model; the Gemini API uses the newer preview.
# Override with --model or the FE_LIVE_MODEL env var for a 3.x live model.
DEFAULT_MODEL_VERTEX = "gemini-2.0-flash-live-preview-04-09"
DEFAULT_MODEL_GEMINI = "gemini-live-2.5-flash-preview"

OBSERVE_EVERY_S = 3          # request a structured report every N frames/seconds
CAMERA_ID = "cctv"          # stamped onto observations; set to the real camera id


# ---------------------------------------------------------------------------
# Response parsing — model JSON -> shared Observation
# ---------------------------------------------------------------------------

_VIDEO_SIGNALS = {
    "stationary_car_visual": SignalType.STATIONARY_CAR_VISUAL,
    "debris": SignalType.DEBRIS,
    "smoke_or_dust": SignalType.SMOKE_OR_DUST,
    "contact": SignalType.CONTACT,
}


def parse_report(text: str, ts_utc: datetime, camera_id: str) -> Optional[Observation]:
    """Turn one model JSON reply into a video Observation, or None if no incident."""
    blob = _extract_json(text)
    if not blob or not blob.get("incident"):
        return None
    signal = _VIDEO_SIGNALS.get(str(blob.get("signal")))
    if signal is None:
        return None
    cars = [int(c) for c in (blob.get("car_numbers") or []) if str(c).lstrip("-").isdigit()]
    return Observation(
        modality=Modality.VIDEO,
        signal=signal,
        ts_utc=ts_utc,
        car_number=cars[0] if cars else None,
        confidence=float(blob.get("confidence", 0.5) or 0.5),
        severity_hint=int(blob.get("severity", 0) or 0),
        location=TrackLocation(camera_id=camera_id),
        summary=str(blob.get("summary", "")).strip(),
        evidence={"car_numbers": cars, "location_hint": blob.get("location_hint")},
    )


def _extract_json(text: str) -> Optional[dict]:
    """Pull the first JSON object out of a model reply (tolerates code fences)."""
    if not text:
        return None
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s : e + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# The live observing loop
# ---------------------------------------------------------------------------

async def observe(
    clip_path: str,
    clip_start_utc: datetime,
    *,
    model: Optional[str] = None,
    camera_id: str = CAMERA_ID,
    observe_every_s: int = OBSERVE_EVERY_S,
    on_observation: Optional[Callable[[Observation], None]] = None,
) -> list[Observation]:
    """Stream the clip through Gemini Live; return the video Observations found."""
    from google import genai              # imported here so --dry-run needs no SDK
    import PIL.Image

    use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() == "true"
    model = model or os.environ.get("FE_LIVE_MODEL") or (
        DEFAULT_MODEL_VERTEX if use_vertex else DEFAULT_MODEL_GEMINI
    )
    frames = extract_frames(clip_path, clip_start_utc)
    client = genai.Client()
    config = {
        "response_modalities": ["TEXT"],
        "system_instruction": prompts.SYSTEM_INSTRUCTION,
    }

    found: list[Observation] = []
    async with client.aio.live.connect(model=model, config=config) as session:
        for f in frames:
            await session.send_realtime_input(media=PIL.Image.open(f.path))
            if f.index % observe_every_s != 0:
                continue
            await session.send_client_content(
                turns={"role": "user", "parts": [{"text": prompts.OBSERVE_REQUEST}]},
                turn_complete=True,
            )
            reply = await _collect_text(session)
            obs = parse_report(reply, f.ts_utc, camera_id)
            if obs:
                found.append(obs)
                if on_observation:
                    on_observation(obs)
    return found


async def _collect_text(session) -> str:
    """Read one model turn to completion, concatenating text parts."""
    chunks: list[str] = []
    async for msg in session.receive():
        if getattr(msg, "text", None):
            chunks.append(msg.text)
        sc = getattr(msg, "server_content", None)
        if sc and getattr(sc, "turn_complete", False):
            break
    return "".join(chunks)


# ---------------------------------------------------------------------------
# Dry run — the pipeline minus the model call
# ---------------------------------------------------------------------------

def dry_run(clip_path: str, clip_start_utc: datetime, observe_every_s: int) -> list[Frame]:
    frames = extract_frames(clip_path, clip_start_utc)
    asks = [f for f in frames if f.index % observe_every_s == 0]
    print(f"Extracted {len(frames)} frames at 1 FPS from {os.path.basename(clip_path)}")
    print(f"Frame 0 @ {frames[0].ts_utc:%H:%M:%S} UTC → "
          f"frame {frames[-1].index} @ {frames[-1].ts_utc:%H:%M:%S} UTC")
    print(f"Would request a structured report on {len(asks)} frames "
          f"(every {observe_every_s}s): "
          f"{', '.join(f'{f.ts_utc:%H:%M:%S}' for f in asks[:8])}"
          f"{' ...' if len(asks) > 8 else ''}")
    print(f"System instruction: {len(prompts.SYSTEM_INSTRUCTION)} chars; "
          f"observe request: {len(prompts.OBSERVE_REQUEST)} chars")
    return frames


def _parse_start(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main() -> int:
    ap = argparse.ArgumentParser(description="Gemini Live 1 FPS video observer")
    ap.add_argument("clip", help="CCTV MP4 path")
    ap.add_argument("--start", required=True,
                    help="wall-clock UTC of the first frame, ISO8601 (e.g. 2024-05-12T13:32:00Z)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--camera-id", default=CAMERA_ID)
    ap.add_argument("--every", type=int, default=OBSERVE_EVERY_S,
                    help="request a report every N seconds/frames")
    ap.add_argument("--dry-run", action="store_true",
                    help="extract + timestamp + assemble prompts, no model call")
    args = ap.parse_args()
    start = _parse_start(args.start)

    if args.dry_run:
        dry_run(args.clip, start, args.every)
        return 0

    def show(o: Observation) -> None:
        print(f"  {o.ts_utc:%H:%M:%S} [{o.signal.value:<22}] "
              f"conf={o.confidence:.2f} sev={o.severity_hint:>3}  {o.summary}")

    obs = asyncio.run(observe(
        args.clip, start, model=args.model,
        camera_id=args.camera_id, observe_every_s=args.every, on_observation=show,
    ))
    print(f"\n{len(obs)} video observation(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
