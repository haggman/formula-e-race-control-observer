"""Simulator configuration — read once at startup. Mirrors the Ch2 shape."""
import os


class Config:
    PROJECT_ID: str = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

    # Pub/Sub topic the telemetry frames go to (state writer + telemetry observer
    # both subscribe).
    PUBSUB_TOPIC: str = os.environ.get("PUBSUB_TOPIC", "fe-telemetry")

    # Frames artifact. Bundled in the image by default; override with a gs:// URI
    # (FRAMES_GCS) to load from the class bucket instead.
    FRAMES_PATH: str = os.environ.get(
        "FRAMES_PATH",
        os.path.join(os.path.dirname(__file__), "frames.jsonl.gz"),
    )
    FRAMES_GCS: str = os.environ.get("FRAMES_GCS", "")   # e.g. gs://class-demo/...

    REPLAY_SPEED_MULTIPLIER: float = float(
        os.environ.get("REPLAY_SPEED_MULTIPLIER", "1.0")
    )
    AUTO_RESTART_DEFAULT: bool = (
        os.environ.get("AUTO_RESTART", "false").lower() == "true"
    )
    RACE_ID: str = os.environ.get("RACE_ID", "berlin_2024_r10")

    def __init__(self):
        if not self.PROJECT_ID:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT env var is required "
                "(set in Cloud Run or via gcloud config)."
            )


config = Config()
