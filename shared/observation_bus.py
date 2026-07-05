"""The observation bus — how observers' Observations reach the correlator.

Both observers publish their Observations to one Pub/Sub topic (fe-observations);
the correlator subscribes and fuses them. Pub/Sub keeps the three agents
decoupled and independently lifecycle-controlled (start/stop each on its own),
and it's the same transport the telemetry stream already uses.
"""
from __future__ import annotations

import logging
import os
from typing import Callable

from shared.models import Observation

logger = logging.getLogger("observation_bus")

OBSERVATIONS_TOPIC = "fe-observations"


class ObservationPublisher:
    """Publishes Observations to the fe-observations topic (idempotent topic)."""

    def __init__(self, project: str | None = None, topic: str = OBSERVATIONS_TOPIC):
        from google.cloud import pubsub_v1
        from google.api_core import exceptions

        self.project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not self.project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT required to publish observations")
        self._pub = pubsub_v1.PublisherClient()
        self._topic_path = self._pub.topic_path(self.project, topic)
        try:
            self._pub.create_topic(name=self._topic_path)
            logger.info("created topic %s", topic)
        except exceptions.AlreadyExists:
            pass

    def publish(self, obs: Observation) -> None:
        self._pub.publish(self._topic_path, obs.model_dump_json().encode("utf-8"))


def make_emit(project: str | None = None, *, also: Callable[[Observation], None] | None = None
              ) -> Callable[[Observation], None]:
    """Return an emit(obs) that publishes to the bus (and optionally also runs
    `also`, e.g. the console print)."""
    pub = ObservationPublisher(project)

    def emit(obs: Observation) -> None:
        if also:
            also(obs)
        pub.publish(obs)
    return emit


def subscribe(
    callback: Callable[[Observation], None],
    *,
    project: str | None = None,
    subscription: str = "fe-observations-correlator-sub",
    topic: str = OBSERVATIONS_TOPIC,
    seek_now: bool = True,
    max_messages: int = 100,
):
    """Subscribe to the observation bus. Creates the pull subscription if missing
    and (optionally) seeks it to now so a run only sees live Observations.
    Returns (subscriber, streaming_pull_future)."""
    from google.cloud import pubsub_v1
    from google.api_core import exceptions
    from google.protobuf.timestamp_pb2 import Timestamp
    from datetime import datetime, timezone

    project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
    subscriber = pubsub_v1.SubscriberClient()
    sub_path = subscriber.subscription_path(project, subscription)
    topic_path = f"projects/{project}/topics/{topic}"
    try:
        subscriber.create_subscription(request={
            "name": sub_path, "topic": topic_path, "ack_deadline_seconds": 30,
            "message_retention_duration": {"seconds": 600},
        })
    except exceptions.AlreadyExists:
        pass
    except exceptions.NotFound:
        # topic doesn't exist yet — create it, then the subscription
        from google.cloud import pubsub_v1 as _p
        _p.PublisherClient().create_topic(name=topic_path)
        subscriber.create_subscription(request={
            "name": sub_path, "topic": topic_path, "ack_deadline_seconds": 30})
    if seek_now:
        ts = Timestamp(); ts.FromDatetime(datetime.now(timezone.utc))
        subscriber.seek(request={"subscription": sub_path, "time": ts})

    def _cb(message) -> None:
        try:
            obs = Observation.model_validate_json(message.data)
        except Exception as e:
            logger.warning("bad observation dropped: %s", e)
            message.ack()
            return
        callback(obs)
        message.ack()

    flow = pubsub_v1.types.FlowControl(max_messages=max_messages)
    future = subscriber.subscribe(sub_path, callback=_cb, flow_control=flow)
    logger.info("subscribed to %s (%s)", topic, subscription)
    return subscriber, future
