"""Simulator FastAPI entrypoint — loads frames, starts the publisher, and exposes
the replay control API (status / pause / resume / speed / jump / restart).

Borrowed from the Ch2 simulator; same control surface so the run-of-show and
demo choreography carry over.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .config import config
from .frame_loader import load_frames
from .publisher import Publisher

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

publisher: Publisher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global publisher
    logger.info("project=%s topic=%s", config.PROJECT_ID, config.PUBSUB_TOPIC)
    publisher = Publisher(load_frames())
    publisher.start()
    yield
    publisher.stop()


app = FastAPI(title="Formula E Race Control — Telemetry Simulator", lifespan=lifespan)


class SpeedRequest(BaseModel):
    multiplier: float


class JumpRequest(BaseModel):
    race_time_s: float


class AutoRestartRequest(BaseModel):
    enabled: bool


def _pub() -> Publisher:
    if publisher is None:
        raise HTTPException(503, "publisher not initialized")
    return publisher


@app.get("/health")
def health():
    return {"ok": True, "service": "simulator"}


@app.get("/status")
def status():
    return _pub().status()


@app.get("/schema")
def schema():
    """A representative mid-race frame, so observer devs know the shape."""
    p = _pub()
    if not p.frames:
        raise HTTPException(503, "frames not loaded")
    return p.frames[len(p.frames) // 2]


@app.post("/restart")
def restart():
    _pub().restart_replay()
    return {"ok": True, "race_time_s": _pub().clock.race_time_s()}


@app.post("/pause")
def pause():
    _pub().clock.pause()
    return {"ok": True, "paused": True, "race_time_s": _pub().clock.race_time_s()}


@app.post("/resume")
def resume():
    _pub().clock.resume()
    return {"ok": True, "paused": False, "race_time_s": _pub().clock.race_time_s()}


@app.post("/speed")
def set_speed(req: SpeedRequest):
    if req.multiplier <= 0:
        raise HTTPException(400, "multiplier must be positive")
    _pub().clock.set_speed(req.multiplier)
    return {"ok": True, "speed_multiplier": _pub().clock.speed()}


@app.post("/jump")
def jump(req: JumpRequest):
    p = _pub()
    p.clock.jump(req.race_time_s)
    p._last_published_tick = int(req.race_time_s) - 1
    return {"ok": True, "race_time_s": p.clock.race_time_s()}


@app.post("/auto-restart")
def set_auto_restart(req: AutoRestartRequest):
    _pub().auto_restart = req.enabled
    return {"ok": True, "auto_restart": _pub().auto_restart}
