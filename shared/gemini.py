"""One place to build the Gemini client, so every agent hits the right endpoint.

Newer Vertex models (Gemini 3.x, e.g. gemini-3.5-flash) are served on the GLOBAL
endpoint, not regional ones — a us-central1 client 404s on them. So on Vertex we
build the client with location=global (covers old and new models); override with
FE_VERTEX_LOCATION if you need a specific region. Off Vertex, it's the Gemini
Developer API (GOOGLE_API_KEY).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time

logger = logging.getLogger("gemini")


def make_client():
    """Return a genai.Client aimed at the right backend + endpoint."""
    from google import genai

    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "").lower() in ("1", "true"):
        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("FE_VERTEX_LOCATION", "global"),
        )
    return genai.Client()          # Gemini Developer API via GOOGLE_API_KEY


# ---------------------------------------------------------------------------
# Retry with exponential backoff — wrap every generate_content call so a
# transient 429/503/timeout doesn't kill a long run (or a full-race catalogue).
# Defaults: ~10 quick tries, 0.5s→8s exponential backoff with jitter (worst case
# under a minute), retrying only on rate-limit / server / network errors.
# Tunable via env: FE_GEMINI_RETRIES, FE_GEMINI_BACKOFF_BASE, FE_GEMINI_BACKOFF_CAP.
# ---------------------------------------------------------------------------
_MAX_TRIES = int(os.environ.get("FE_GEMINI_RETRIES", "10"))
_BASE_S = float(os.environ.get("FE_GEMINI_BACKOFF_BASE", "0.5"))
_CAP_S = float(os.environ.get("FE_GEMINI_BACKOFF_CAP", "8.0"))
_RETRY_CODES = {408, 409, 429, 500, 502, 503, 504}
_RETRY_TOKENS = ("RESOURCE_EXHAUSTED", "UNAVAILABLE", "INTERNAL", "DEADLINE",
                 "TIMEOUT", "TIMED OUT", "CONNECTION", "RESET", "TEMPORARIL",
                 "OVERLOADED", "429", "500", "502", "503", "504")


def _status_code(exc: Exception):
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    v = getattr(resp, "status_code", None)
    return v if isinstance(v, int) else None


def _retryable(exc: Exception) -> bool:
    code = _status_code(exc)
    if code is not None:
        return code in _RETRY_CODES
    s = str(exc).upper()
    return any(tok in s for tok in _RETRY_TOKENS)


def _backoff_s(attempt: int) -> float:
    """Exponential base*2^attempt, capped, with 50–100% jitter."""
    return min(_CAP_S, _BASE_S * (2 ** attempt)) * (0.5 + random.random() * 0.5)


def retry_call(fn, *, tries: int | None = None, what: str = "gemini"):
    """Call a zero-arg `fn`, retrying transient failures with backoff (sync)."""
    tries = tries or _MAX_TRIES
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:                       # noqa: BLE001
            if attempt == tries - 1 or not _retryable(e):
                raise
            delay = _backoff_s(attempt)
            logger.warning("%s call failed (%s) — retry %d/%d in %.1fs",
                           what, e, attempt + 1, tries - 1, delay)
            time.sleep(delay)


async def aretry_call(afn, *, tries: int | None = None, what: str = "gemini"):
    """Await a zero-arg coroutine factory `afn`, retrying transients (async)."""
    tries = tries or _MAX_TRIES
    for attempt in range(tries):
        try:
            return await afn()
        except Exception as e:                       # noqa: BLE001
            if attempt == tries - 1 or not _retryable(e):
                raise
            delay = _backoff_s(attempt)
            logger.warning("%s call failed (%s) — retry %d/%d in %.1fs",
                           what, e, attempt + 1, tries - 1, delay)
            await asyncio.sleep(delay)
