"""Race Control console — FastAPI backend.

The live hub for the UI: it subscribes to BOTH buses (fe-observations for the two
raw sensor feeds, fe-incidents for the correlator's fused recommendations),
polls Firestore race_states for live car positions, and streams everything to the
browser over one WebSocket. It also serves the console, proxies the sim controls
(jump/pause/resume/speed/restart), and records the official's Approve/Reject.

Run locally:  (after source activate.sh, with SIM_URL set)
    uvicorn frontend.main:app --host 0.0.0.0 --port 8080
Then open http://localhost:8080  (in Cloud Shell: Web Preview on port 8080).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared import observation_bus                                            # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("console")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT")
RACE_ID = os.environ.get("RACE_ID", "berlin_2024_r10")
SIM_URL = os.environ.get("SIM_URL", "").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Formula E — Race Control Console")

# --- live client fan-out ---------------------------------------------------
_clients: set[WebSocket] = set()
_queue: "asyncio.Queue[dict]" = asyncio.Queue()
_loop: asyncio.AbstractEventLoop | None = None


def _push(event: dict) -> None:
    """Thread-safe enqueue from Pub/Sub callback threads → the asyncio loop."""
    if _loop is not None:
        _loop.call_soon_threadsafe(_queue.put_nowait, event)


async def _broadcaster() -> None:
    while True:
        event = await _queue.get()
        dead = []
        for ws in _clients:
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            _clients.discard(ws)


# --- bus subscriptions (run in Pub/Sub's background threads) ---------------
def _on_observation(obs) -> None:
    loc = obs.location
    _push({"type": "observation", "data": {
        "modality": obs.modality.value, "signal": obs.signal.value,
        "ts_utc": obs.ts_utc.isoformat(), "car_number": obs.car_number,
        "severity": obs.severity_hint, "confidence": obs.confidence,
        "camera_id": loc.camera_id, "gps": ([loc.gps_lat, loc.gps_lng]
                                            if loc.gps_lat is not None else None),
        "cars": obs.evidence.get("cars", []),   # flagged car(s), for the car chips
        "summary": obs.summary,
    }})


def _subscribe_incidents() -> None:
    """Own subscription on fe-incidents; forward {kind, report} to clients."""
    from google.cloud import pubsub_v1
    from google.api_core import exceptions
    from google.protobuf.timestamp_pb2 import Timestamp
    from datetime import datetime, timezone

    sub = pubsub_v1.SubscriberClient()
    sub_path = sub.subscription_path(PROJECT_ID, "fe-incidents-console-sub")
    topic_path = f"projects/{PROJECT_ID}/topics/{observation_bus.INCIDENTS_TOPIC}"
    try:
        sub.create_subscription(request={"name": sub_path, "topic": topic_path,
                                         "ack_deadline_seconds": 30})
    except exceptions.AlreadyExists:
        pass
    except exceptions.NotFound:
        from google.cloud import pubsub_v1 as _p
        _p.PublisherClient().create_topic(name=topic_path)
        sub.create_subscription(request={"name": sub_path, "topic": topic_path})
    ts = Timestamp(); ts.FromDatetime(datetime.now(timezone.utc))
    sub.seek(request={"subscription": sub_path, "time": ts})

    def cb(msg):
        try:
            payload = json.loads(msg.data)
            _push({"type": "incident", "data": payload})
        except Exception as e:
            logger.warning("bad incident msg: %s", e)
        msg.ack()

    sub.subscribe(sub_path, callback=cb)


async def _poll_cars() -> None:
    """Poll race_states 'now' for live car positions and push them at ~1 Hz."""
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
    except Exception as e:
        logger.warning("Firestore unavailable — no live cars (%s)", e)
        return
    ref = db.collection("race_states").document(RACE_ID)
    while True:
        try:
            doc = ref.get()
            if doc.exists:
                d = doc.to_dict()
                cars = [{"n": c["car_number"], "lat": c["lat"], "lng": c["lng"],
                         "retired": c.get("is_retired", False)}
                        for c in d.get("cars", [])]
                _push({"type": "cars", "race_time_s": d.get("race_time_s"), "cars": cars})
        except Exception as e:
            logger.debug("car poll: %s", e)
        await asyncio.sleep(1.0)


async def _poll_agents() -> None:
    """Poll agent_status heartbeats → push per-agent online/warming state at ~0.5 Hz."""
    import time as _t
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
    except Exception as e:
        logger.warning("Firestore unavailable — no agent status (%s)", e)
        return
    while True:
        try:
            now = int(_t.time())
            agents = {}
            for d in db.collection("agent_status").stream():
                x = d.to_dict()
                agents[x.get("name", d.id)] = {
                    "state": x.get("state", "?"), "detail": x.get("detail", ""),
                    "online": (now - int(x.get("updated_at_unix", 0))) < 15,
                }
            _push({"type": "agents", "agents": agents})
        except Exception as e:
            logger.debug("agent poll: %s", e)
        await asyncio.sleep(2.0)


@app.on_event("startup")
async def _startup() -> None:
    global _loop
    _loop = asyncio.get_running_loop()
    asyncio.create_task(_broadcaster())
    asyncio.create_task(_poll_cars())
    asyncio.create_task(_poll_agents())
    if PROJECT_ID:
        observation_bus.subscribe(_on_observation, project=PROJECT_ID,
                                  subscription="fe-observations-console-sub")
        _subscribe_incidents()
    logger.info("console up (project=%s sim=%s)", PROJECT_ID, SIM_URL or "(unset)")


# --- pages + assets --------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    return open(os.path.join(HERE, "static", "index.html")).read()


app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    _clients.add(sock)
    # replay current incidents from Firestore so a late-joining browser is caught up
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
        for d in db.collection("incidents").stream():
            await sock.send_json({"type": "incident",
                                  "data": {"kind": "SNAPSHOT", "report": d.to_dict()}})
    except Exception:
        pass
    try:
        while True:
            await sock.receive_text()      # keepalive / ignore client pings
    except WebSocketDisconnect:
        _clients.discard(sock)


# --- sim controls (proxy) --------------------------------------------------
async def _sim_post(path: str, body: dict | None = None) -> dict:
    if not SIM_URL:
        return {"ok": False, "error": "SIM_URL not set"}
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(f"{SIM_URL}{path}", json=body or {})
        return r.json()


async def _sim_race_time() -> float | None:
    if not SIM_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            return (await c.get(f"{SIM_URL}/status")).json().get("race_time_s")
    except Exception:
        return None


# --- server-side auto-pause at the end of a jumped incident window ---------
# Done here (not in the browser) so it fires even when the console tab is
# backgrounded, throttled, or closed. The console reflects it via the
# "autopaused" push.
_autopause_task: "asyncio.Task | None" = None


def _cancel_autopause() -> None:
    global _autopause_task
    if _autopause_task and not _autopause_task.done():
        _autopause_task.cancel()
    _autopause_task = None


async def _autopause_watch(pause_at: float) -> None:
    try:
        while True:
            await asyncio.sleep(1.0)
            rt = await _sim_race_time()
            if rt is not None and rt >= pause_at:
                await _sim_post("/pause")
                _push({"type": "autopaused", "race_time_s": rt})
                logger.info("auto-paused at race_time %.0f (window end)", rt)
                return
    except asyncio.CancelledError:
        return


def _schedule_autopause(pause_at) -> None:
    _cancel_autopause()
    if pause_at is not None:
        global _autopause_task
        _autopause_task = asyncio.create_task(_autopause_watch(float(pause_at)))


@app.post("/control/jump")
async def jump(body: dict):
    """Jump to a flag point: pause → jump → resume (so the incident replays live),
    and arm a server-side auto-pause at the end of the window if requested."""
    await _sim_post("/pause")
    await _sim_post("/jump", {"race_time_s": body.get("race_time_s", 0)})
    _schedule_autopause(body.get("pause_at"))
    return await _sim_post("/resume")


@app.get("/control/status")
async def sim_status():
    """Proxy the sim's /status so the UI can reflect paused/speed/clock."""
    if not SIM_URL:
        return {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            return (await c.get(f"{SIM_URL}/status")).json()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/control/pause")
async def pause():
    return await _sim_post("/pause")


@app.post("/control/resume")
async def resume():
    return await _sim_post("/resume")


@app.post("/control/restart")
async def restart():
    _cancel_autopause()
    return await _sim_post("/restart")


@app.post("/control/speed")
async def speed(body: dict):
    return await _sim_post("/speed", {"multiplier": body.get("multiplier", 1.0)})


# --- clear the board (wipe stale incidents so snapshots don't resurrect) ---
@app.post("/incidents/clear")
async def clear_incidents():
    _cancel_autopause()
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
        n = 0
        for d in db.collection("incidents").stream():
            d.reference.delete()
            n += 1
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    _push({"type": "cleared"})
    return {"ok": True, "deleted": n}


# --- one-click approve / reject -------------------------------------------
@app.post("/incident/{incident_id}/{decision}")
async def decide(incident_id: str, decision: str):
    approved = decision == "approve"
    try:
        from google.cloud import firestore
        db = firestore.Client(project=PROJECT_ID)
        db.collection("incidents").document(incident_id).set(
            {"approved": approved}, merge=True)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    _push({"type": "decision", "incident_id": incident_id, "approved": approved})
    return {"ok": True, "incident_id": incident_id, "approved": approved}


# --- entrypoint ------------------------------------------------------------
# `python -m frontend.main` works identically in Cloud Shell (PORT unset → 8080,
# reachable via Web Preview) and in a Cloud Run container (Cloud Run injects PORT).
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
