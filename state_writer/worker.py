"""State Writer worker — Cloud Run Worker Pool (Pub/Sub PULL).

Borrowed from the Ch2 fan-concierge worker. A Pub/Sub-pull workload has no request
surface, so a worker pool (not an HTTP service) is the right shape: it opens a
StreamingPull on the subscription, decodes each frame, and calls the idempotent
write_frame. Redelivery is safe because the write is an overwrite.

Env:
  GOOGLE_CLOUD_PROJECT (or PROJECT_ID)
  SUBSCRIPTION_NAME   pull subscription (default fe-state-writer-sub)
  MAX_MESSAGES        flow-control cap (default 100)
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading

from google.cloud import pubsub_v1

from state_writer.writer_core import write_frame

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("state_writer.worker")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
if not PROJECT_ID:
    raise RuntimeError("GOOGLE_CLOUD_PROJECT (or PROJECT_ID) env var required")

SUBSCRIPTION_NAME = os.environ.get("SUBSCRIPTION_NAME", "fe-state-writer-sub")
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "100"))


def _callback(message: "pubsub_v1.subscriber.message.Message") -> None:
    try:
        frame = json.loads(message.data)
    except (ValueError, json.JSONDecodeError) as e:
        logger.warning("dropping malformed message %s: %s", message.message_id, e)
        message.ack()
        return
    try:
        write_frame(frame)
        message.ack()
    except Exception as e:
        logger.exception("write failed, will redeliver (%s): %s", message.message_id, e)
        message.nack()


def main() -> None:
    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(PROJECT_ID, SUBSCRIPTION_NAME)
    flow = pubsub_v1.types.FlowControl(max_messages=MAX_MESSAGES)
    future = subscriber.subscribe(sub_path, callback=_callback, flow_control=flow)
    logger.info("state writer worker online — pulling %s (project %s)",
                SUBSCRIPTION_NAME, PROJECT_ID)

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    stop.wait()

    logger.info("shutting down — cancelling streaming pull")
    future.cancel()
    try:
        future.result(timeout=30)
    except Exception:
        pass
    subscriber.close()


if __name__ == "__main__":
    main()
