"""Video Observer — the clock-gated Gemini Live observer (Agent 1).

Watches one 2x2 camera mosaic at ~1 frame/second, driven by the race clock, and
emits video Observations for safety incidents. This is the one Gemini spender, so
its lifecycle is the point:

  - CLOCK-GATED Live session. It holds a Gemini Live session ONLY while the
    simulator's race_time_s is advancing. When the sim pauses or ends, it CLOSES
    the session (token burn stops) and keeps polling cheaply; when the clock moves
    again it reopens. So launching/pausing the simulator is the on/off switch.
  - Deadman backstop (default 10 min) via the shared Session — the ultimate cap if
    the UI dies without signalling. It does NOT idle-exit, so a brief pause won't
    kill the process; the deadman + explicit SIGTERM are the only hard stops.

Design note (Live API): frames + the report request are sent as ONE turn-based
`send_client_content` call (the docs discourage interleaving send_realtime_input
with send_client_content). `_observe_once` isolates that strategy so it can be
swapped for pure realtime streaming if testing prefers it.

Run (after `source activate.sh`, with the simulator publishing):
    python -m observers.video.observer --group grp_01_cam01_cam02_cam03_cam04
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.models import Modality, Observation, SignalType, TrackLocation  # noqa: E402
from shared.lifecycle import Session                                        # noqa: E402
from observers.video import prompts                                         # noqa: E402
from observers.video.clock import SimClock                                  # noqa: E402
from observers.video.mosaic_source import MosaicSource                      # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("video.observer")

# Standard (half-cascade) Live model — built for video/image/text IN, TEXT out,
# which is exactly our silent-CCTV -> JSON case (better than the audio-first
# native-audio model). Prefer the newest your project has via FE_LIVE_MODEL:
#   gemini-3.1-flash-live  (newest)  >  gemini-2.5-flash-live  >  native-audio.
DEFAULT_MODEL_VERTEX = "gemini-2.5-flash-live"
DEFAULT_MODEL_GEMINI = "gemini-live-2.5-flash-preview"
OBSERVE_EVERY_S = 3          # request a structured report every N race-seconds
GREEN_FLAG = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)  # race_time_s=0

_VIDEO_SIGNALS = {
    "stationary_car_visual": SignalType.STATIONARY_CAR_VISUAL,
    "debris": SignalType.DEBRIS,
    "smoke_or_dust": SignalType.SMOKE_OR_DUST,
    "contact": SignalType.CONTACT,
}


def _extract_json(text: str) -> Optional[dict]:
    if not text:
        return None
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e <= s:
        return None
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None


def parse_report(text: str, ts_utc: datetime) -> Optional[Observation]:
    """Turn one model JSON reply into a video Observation, or None."""
    blob = _extract_json(text)
    if not blob or not blob.get("incident"):
        return None
    signal = _VIDEO_SIGNALS.get(str(blob.get("signal")))
    if signal is None:
        return None
    cars = [int(c) for c in (blob.get("car_numbers") or []) if str(c).lstrip("-").isdigit()]
    cam = blob.get("camera_id")
    return Observation(
        modality=Modality.VIDEO, signal=signal, ts_utc=ts_utc,
        car_number=cars[0] if cars else None,
        confidence=float(blob.get("confidence", 0.5) or 0.5),
        severity_hint=int(blob.get("severity", 0) or 0),
        location=TrackLocation(camera_id=cam),
        summary=str(blob.get("summary", "")).strip(),
        evidence={"car_numbers": cars, "camera_id": cam},
    )


class VideoObserver:
    """Clock-gated Gemini Live observer over one 2x2 mosaic."""

    def __init__(
        self,
        clock: SimClock,
        mosaic: MosaicSource,
        *,
        model: Optional[str] = None,
        observe_every_s: int = OBSERVE_EVERY_S,
        emit: Callable[[Observation], None] = lambda o: print(o.model_dump_json()),
    ):
        self.clock = clock
        self.mosaic = mosaic
        self.observe_every_s = observe_every_s
        self.emit = emit
        use_vertex = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true")
        self.model = model or os.environ.get("FE_LIVE_MODEL") or (
            DEFAULT_MODEL_VERTEX if use_vertex else DEFAULT_MODEL_GEMINI)
        self._client = None
        self._live_cm = None
        self._session = None            # the open Gemini Live session, or None
        self._last_processed = -1       # last race-second sent to the model

    # -- Gemini Live session management (clock-gated open/close) --------------
    async def _open_live(self) -> None:
        if self._session is not None:
            return
        from google import genai
        if self._client is None:
            self._client = genai.Client()
        config = {
            "response_modalities": ["TEXT"],
            "system_instruction": prompts.system_instruction(self.mosaic.panels),
        }
        self._live_cm = self._client.aio.live.connect(model=self.model, config=config)
        self._session = await self._live_cm.__aenter__()
        logger.info("Live session opened (%s)", self.model)

    async def _close_live(self) -> None:
        if self._session is None:
            return
        try:
            await self._live_cm.__aexit__(None, None, None)
        except Exception:
            pass
        self._session = None
        self._live_cm = None
        logger.info("Live session closed (clock paused / stopping) — Gemini idle")

    # -- one observation turn (the swappable strategy) -----------------------
    async def _observe_once(self, race_seconds: list[int]) -> None:
        """Send the frames for `race_seconds` + the report request as one turn."""
        from google.genai import types

        parts = []
        for s in race_seconds:
            fp = self.mosaic.frame_path(s)
            if fp:
                parts.append(types.Part.from_bytes(
                    data=open(fp, "rb").read(), mime_type="image/jpeg"))
        if not parts:
            return
        parts.append(types.Part(text=prompts.OBSERVE_REQUEST))

        await self._session.send_client_content(
            turns=types.Content(role="user", parts=parts), turn_complete=True)

        chunks: list[str] = []
        async for msg in self._session.receive():
            if getattr(msg, "text", None):
                chunks.append(msg.text)
            sc = getattr(msg, "server_content", None)
            if sc and getattr(sc, "turn_complete", False):
                break
        ts = GREEN_FLAG + timedelta(seconds=race_seconds[-1])
        obs = parse_report("".join(chunks), ts)
        if obs:
            self.emit(obs)

    # -- the main clock-gated loop -------------------------------------------
    async def run(self, session: Session) -> str:
        try:
            while session.active():
                sample = self.clock.read()
                advancing = sample.reachable and self.clock.is_advancing()

                if not advancing:
                    await self._close_live()          # pause/ended → stop Gemini
                    await asyncio.sleep(1.0)
                    continue

                session.touch()
                await self._open_live()
                now_s = int(sample.race_time_s)
                # process each new race-second since last, in observe batches
                pending = [s for s in range(self._last_processed + 1, now_s + 1)]
                for i in range(0, len(pending), self.observe_every_s):
                    batch = pending[i:i + self.observe_every_s]
                    if len(batch) == self.observe_every_s or batch[-1] == now_s:
                        await self._observe_once(batch)
                        self._last_processed = batch[-1]
                await asyncio.sleep(1.0)
        finally:
            await self._close_live()
        return session.stop_reason or "stopped"


def _resolve_mosaic(group: str, bucket: Optional[str], local: Optional[str]) -> tuple[str, str]:
    """Return (mosaic_ref, manifest_ref) from a local dir or the staged bucket."""
    if local:
        return os.path.join(local, f"{group}.mp4"), os.path.join(local, "manifest.json")
    bucket = bucket or os.environ.get("MOSAICS_BUCKET")
    if not bucket:
        raise RuntimeError("need --local DIR or MOSAICS_BUCKET / --bucket")
    base = f"gs://{bucket}/mosaics"
    return f"{base}/{group}.mp4", f"{base}/manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Clock-gated Gemini Live video observer")
    ap.add_argument("--group", required=True, help="mosaic group_id to watch")
    ap.add_argument("--bucket", default=None, help="mosaics bucket (default $MOSAICS_BUCKET)")
    ap.add_argument("--local", default=None, help="local dir of mosaics instead of a bucket")
    ap.add_argument("--sim-url", default=os.environ.get("SIM_URL", ""))
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-runtime", type=float, default=600.0, help="deadman backstop seconds")
    ap.add_argument("--every", type=int, default=OBSERVE_EVERY_S)
    args = ap.parse_args()
    if not args.sim_url:
        ap.error("--sim-url (or SIM_URL) required")

    mosaic_ref, manifest_ref = _resolve_mosaic(args.group, args.bucket, args.local)
    mosaic = MosaicSource(mosaic_ref=mosaic_ref, group_id=args.group,
                          manifest_ref=manifest_ref).prepare()
    clock = SimClock(args.sim_url)
    observer = VideoObserver(clock, mosaic, model=args.model, observe_every_s=args.every)

    def show(o: Observation) -> None:
        print(f"  {o.ts_utc:%H:%M:%S} [{o.signal.value:<22}] cam={o.location.camera_id} "
              f"conf={o.confidence:.2f} sev={o.severity_hint:>3}  {o.summary}")
    observer.emit = show

    # Video observer: deadman backstop ONLY (no idle-exit — a pause must not kill
    # it; the clock gate closes the Live session instead).
    with Session(max_runtime_s=(args.max_runtime or None), idle_timeout_s=None,
                 name="video") as sess:
        reason = asyncio.run(observer.run(sess))
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
