"""Load the 1 Hz frames artifact into memory (bundled file or GCS)."""
from __future__ import annotations

import gzip
import json
import logging
from typing import Any

from .config import config

logger = logging.getLogger(__name__)


def load_frames() -> list[dict[str, Any]]:
    """Return frames as plain dicts, sorted by race_time_s.

    Loads from the bundled frames.jsonl.gz unless FRAMES_GCS is set, in which case
    it pulls the artifact from the class bucket. Each line is one RaceFrame JSON.
    """
    if config.FRAMES_GCS:
        raw = _read_gcs(config.FRAMES_GCS)
    else:
        with gzip.open(config.FRAMES_PATH, "rt", encoding="utf-8") as f:
            raw = f.read()

    frames = [json.loads(line) for line in raw.splitlines() if line.strip()]
    frames.sort(key=lambda fr: fr["race_time_s"])
    logger.info("loaded %d frames (%s..%s)", len(frames),
                frames[0]["race_time_s"], frames[-1]["race_time_s"])
    return frames


def _read_gcs(gcs_uri: str) -> str:
    from google.cloud import storage

    assert gcs_uri.startswith("gs://")
    bucket_name, _, blob_name = gcs_uri[len("gs://"):].partition("/")
    client = storage.Client(project=config.PROJECT_ID)
    data = client.bucket(bucket_name).blob(blob_name).download_as_bytes()
    return gzip.decompress(data).decode("utf-8") if gcs_uri.endswith(".gz") else data.decode("utf-8")
