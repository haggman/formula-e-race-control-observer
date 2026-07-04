"""Write one RaceFrame to Firestore — the shared core (transport-agnostic).

Idempotent by construction: the current frame overwrites race_states/{race_id},
so Pub/Sub at-least-once redelivery and full race replays converge onto the same
doc instead of piling up. This is the "now" the Race Control console reads for its
live track map; the telemetry observer runs off the stream directly.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from google.cloud import firestore

from shared.models import RaceFrame

logger = logging.getLogger("state_writer.core")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    raise RuntimeError("GOOGLE_CLOUD_PROJECT (or PROJECT_ID) env var required")

_db = firestore.Client(project=PROJECT_ID)


def write_frame(frame_dict: dict[str, Any]) -> None:
    """Validate a frame and overwrite race_states/{race_id} with it."""
    frame = RaceFrame.model_validate(frame_dict)
    doc = frame.model_dump(mode="json")
    doc["updated_at_unix"] = int(time.time())          # for monitoring
    _db.collection("race_states").document(frame.race_id).set(doc)
    logger.info("frame t=%d phase=%s cars=%d",
                frame.race_time_s, frame.race_phase, len(frame.cars))
