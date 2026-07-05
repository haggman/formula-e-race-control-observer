"""Video Observer — the clock-gated multimodal observer (Agent 1).

Watches one 2x2 camera mosaic at ~1 frame/second, driven by the race clock, and
emits video Observations for safety incidents.

Why multimodal generate_content and not the Live API: on Vertex the only GA Live
model is audio-only ("Text output is not supported for native audio output
model"), and we're staying on Vertex/ADC (no API key). A standard vision model
(gemini-2.5-flash) does image-in / JSON-text-out, which is exactly our need — and
it's simpler and cheaper to gate: there's no persistent session to hold open, so
"pause Gemini when the sim pauses" is just "don't call the model while the clock
is stalled". The 1 FPS feed, the sync, and the Observation contract are unchanged.

Lifecycle: the one Gemini spender, so its cost is gated on the clock. While
race_time_s advances it calls the model every OBSERVE_EVERY_S seconds; when the
sim pauses/ends it makes NO calls (cost = 0) and keeps polling cheaply; the 10-min
deadman is the backstop if the UI dies without signalling. It does NOT idle-exit,
so a brief pause won't kill the process.

Run (after `source activate.sh`, with the simulator publishing):
    python -m observers.video.observer --group grp_01_cam01_cam02_cam03_cam04
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from collections import deque
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
logging.getLogger("google_genai.models").setLevel(logging.WARNING)  # quiet AFC noise
logger = logging.getLogger("video.observer")

# Standard Vertex vision model (image in, JSON text out). Override with
# FE_VIDEO_MODEL if your project serves a different ID.
DEFAULT_MODEL = "gemini-3.5-flash"
WINDOW_S = 10                # send the last N seconds of frames per call (sliding)
SCRATCHPAD_N = 8             # rolling memory: keep the last N reports for continuity
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
    """Clock-gated multimodal observer over one 2x2 mosaic."""

    def __init__(
        self,
        clock: SimClock,
        mosaic: MosaicSource,
        *,
        model: Optional[str] = None,
        window_s: int = WINDOW_S,
        scratchpad_n: int = SCRATCHPAD_N,
        emit: Callable[[Observation], None] = lambda o: print(o.model_dump_json()),
    ):
        self.clock = clock
        self.mosaic = mosaic
        self.window_s = window_s
        self.emit = emit
        self.model = model or os.environ.get("FE_VIDEO_MODEL") or DEFAULT_MODEL
        self._client = None
        self._active = False            # True while watching (clock advancing)
        self._last_processed = -1       # last race-second sent to the model
        self._recent: deque = deque(maxlen=scratchpad_n)  # rolling report memory

    def _ensure_client(self) -> None:
        if self._client is None:
            from shared.gemini import make_client
            self._client = make_client()   # Vertex(global)/ADC, or Gemini API

    # -- one observation (the swappable strategy) ----------------------------
    async def _observe_once(self, race_seconds: list[int]) -> None:
        """Ask the model about the frames for `race_seconds`; emit any incident."""
        from google.genai import types

        parts = []
        for s in race_seconds:
            fp = self.mosaic.frame_path(s)
            if fp:
                parts.append(types.Part.from_bytes(
                    data=open(fp, "rb").read(), mime_type="image/jpeg"))
        if not parts:
            return
        if self._recent:                          # rolling memory → continuity
            parts.append(types.Part(text=prompts.recent_context(list(self._recent))))
        parts.append(types.Part(text=prompts.OBSERVE_REQUEST))

        resp = await self._client.aio.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user", parts=parts)],
            config=types.GenerateContentConfig(
                system_instruction=prompts.system_instruction(self.mosaic.panels),
                temperature=0.2,
                response_mime_type="application/json",
            ),
        )
        ts = GREEN_FLAG + timedelta(seconds=race_seconds[-1])
        obs = parse_report(resp.text or "", ts)
        if obs:
            self._recent.append(
                f"{obs.ts_utc:%H:%M:%S} [{obs.location.camera_id}] "
                f"{obs.summary} (sev {obs.severity_hint})")
            self.emit(obs)

    # -- the clock-gated loop ------------------------------------------------
    async def run(self, session: Session) -> str:
        self._ensure_client()
        while session.active():
            sample = self.clock.read()
            advancing = sample.reachable and self.clock.is_advancing()

            if not advancing:
                if self._active:
                    logger.info("clock paused/ended — video analysis idle (no Gemini calls)")
                    self._active = False
                await asyncio.sleep(1.0)
                continue

            if not self._active:
                logger.info("clock advancing — watching (model=%s)", self.model)
                self._active = True
            session.touch()

            now_s = int(sample.race_time_s)
            # Sliding window: each call looks at the last window_s seconds up to
            # NOW (overlapping consecutive calls for temporal continuity). We
            # anchor on 'now', so a startup or /jump skips straight to the present
            # instead of replaying the backlog.
            if now_s > self._last_processed:
                start = max(0, now_s - self.window_s + 1)
                await self._observe_once(list(range(start, now_s + 1)))
                self._last_processed = now_s
            await asyncio.sleep(1.0)
        return session.stop_reason or "stopped"


async def _run_with_signals(observer: "VideoObserver", sess: Session) -> str:
    """Run under asyncio-native signal handling so Ctrl-C / SIGTERM stop it
    IMMEDIATELY — cancelling the in-flight model call rather than waiting for the
    loop to notice a flag between ~5s calls."""
    loop = asyncio.get_running_loop()
    task = asyncio.ensure_future(observer.run(sess))

    def _stop() -> None:
        logger.info("stop signal — shutting down video observer")
        sess.request_stop("stopped")
        task.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:
            pass  # non-Unix
    try:
        return await task
    except asyncio.CancelledError:
        return sess.stop_reason or "stopped"


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
    ap = argparse.ArgumentParser(description="Clock-gated multimodal video observer")
    ap.add_argument("--group", required=True, help="mosaic group_id to watch")
    ap.add_argument("--bucket", default=None, help="mosaics bucket (default $MOSAICS_BUCKET)")
    ap.add_argument("--local", default=None, help="local dir of mosaics instead of a bucket")
    ap.add_argument("--sim-url", default=os.environ.get("SIM_URL", ""))
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-runtime", type=float, default=600.0, help="deadman backstop seconds")
    ap.add_argument("--window", type=int, default=WINDOW_S,
                    help="seconds of frames to send per call (sliding window)")
    args = ap.parse_args()
    if not args.sim_url:
        ap.error("--sim-url (or SIM_URL) required")

    mosaic_ref, manifest_ref = _resolve_mosaic(args.group, args.bucket, args.local)
    mosaic = MosaicSource(mosaic_ref=mosaic_ref, group_id=args.group,
                          manifest_ref=manifest_ref).prepare()
    clock = SimClock(args.sim_url)
    observer = VideoObserver(clock, mosaic, model=args.model, window_s=args.window)

    def show(o: Observation) -> None:
        print(f"  {o.ts_utc:%H:%M:%S} [{o.signal.value:<22}] cam={o.location.camera_id} "
              f"conf={o.confidence:.2f} sev={o.severity_hint:>3}  {o.summary}")
    observer.emit = show

    # install_signals=False: the async runner installs asyncio-native handlers
    # instead (so Ctrl-C cancels the in-flight call immediately). Deadman backstop
    # only, no idle-exit — a pause must not kill it; the clock gate just stops
    # making model calls.
    with Session(max_runtime_s=(args.max_runtime or None), idle_timeout_s=None,
                 name="video", install_signals=False) as sess:
        reason = asyncio.run(_run_with_signals(observer, sess))
    logger.info("stopped (%s)", reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
