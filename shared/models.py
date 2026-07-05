"""Shared contracts for the Proactive Race Control Observer.

Three agents speak these models:
  - The **Telemetry Observer** and **Video Observer** each emit `Observation`s.
  - The **Correlator** fuses Observations into a `CorrelatedIncident` and drafts
    an `IncidentReport` carrying a `FlagRecommendation` for one-click approval.

Design notes:
  - Time is the join key across three modalities that DON'T share a clock
    exactly (telemetry stop vs. race-control message vs. video frame can differ
    by seconds). Every Observation therefore carries an absolute `ts_utc`; the
    Correlator fuses within a tolerance WINDOW, never by exact-timestamp match.
  - Observations are modality-tagged but share one shape, so the Correlator can
    treat "an observer said something happened near time T at place P" uniformly.
  - Nothing here does I/O. Pure data contracts, same as the other two hacks.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------
# Raw telemetry sample — one 20 Hz row, per car
# ----------------------------------------------------------------------------

class TelemetrySample(BaseModel):
    """One 20 Hz telemetry sample for one car.

    Mirrors the Berlin R10 telemetry columns (tv_* fields), renamed to the
    contract used across this repo. Units: speed km/h, accel g-ish (raw tv_acc),
    yaw_rate deg/s, gps WGS84, heading degrees, brake 0-100%.
    """
    car_number: int
    ts_utc: datetime
    speed_kmh: float
    accel_x: float          # longitudinal (braking/accel) — raw tv_acc_x
    accel_y: float          # lateral (cornering) — raw tv_acc_y
    yaw_rate: float         # deg/s — raw tv_yaw_rate
    brake_pct: float        # 0-100
    lat: float
    lng: float
    heading: float
    driver_name: Optional[str] = None


# ----------------------------------------------------------------------------
# Stream frame — one 1 Hz snapshot of the whole field (the data plane contract)
# ----------------------------------------------------------------------------
#
# The simulator publishes one RaceFrame per race-second to Pub/Sub; the state
# writer stores the latest as "now" in Firestore; the telemetry observer buffers
# a rolling window of these and converts each car to a TelemetrySample for the
# detector. 1 Hz is plenty for the stopped-car trigger (the primary safety
# signal); it borrows the Ch2 simulator shape.

class FrameCar(BaseModel):
    """One car's motion state at one race-second (a downsampled telemetry row)."""
    car_number: int
    driver_name: Optional[str] = None
    position: Optional[int] = None
    speed_kmh: float
    accel_x: float
    accel_y: float
    yaw_rate: float
    brake_pct: float
    lat: float
    lng: float
    heading: float
    is_retired: bool = False


class RaceFrame(BaseModel):
    """One 1 Hz snapshot of the whole field — the unit the simulator publishes.

    `ts_utc` is the REAL race wall-clock for this second (the anchor that keeps
    telemetry and video aligned); `race_time_s` is seconds since green flag (the
    replay clock's unit). The state writer overwrites race_states/{race_id} with
    the latest frame.
    """
    schema_version: str = "1.0"
    race_id: str
    race_time_s: int
    ts_utc: datetime
    race_phase: str = "racing"
    cars: list[FrameCar] = Field(default_factory=list)

    def to_samples(self) -> list["TelemetrySample"]:
        """Convert this frame's cars into TelemetrySamples for the detector."""
        return [
            TelemetrySample(
                car_number=c.car_number, ts_utc=self.ts_utc,
                speed_kmh=c.speed_kmh, accel_x=c.accel_x, accel_y=c.accel_y,
                yaw_rate=c.yaw_rate, brake_pct=c.brake_pct,
                lat=c.lat, lng=c.lng, heading=c.heading,
                driver_name=c.driver_name,
            )
            for c in self.cars
        ]


# ----------------------------------------------------------------------------
# Observations — what an observer reports upstream
# ----------------------------------------------------------------------------

class Modality(str, Enum):
    """Which observer produced an Observation."""
    TELEMETRY = "telemetry"
    VIDEO = "video"


class SignalType(str, Enum):
    """The kind of anomaly an observer flagged.

    Telemetry signals are deterministic (computed from tv_* fields). Video
    signals are what the Live-API model reports seeing. STOPPED_CAR is the
    strongest cross-modal signal — telemetry sees speed→0, video sees the car
    sitting on track — which is exactly why it anchors correlation.
    """
    # Telemetry-derived
    STOPPED_CAR = "stopped_car"          # sustained speed ~0 (retirement / stopped on track)
    PROLONGED_STOP = "prolonged_stop"    # STILL stopped after the escalation hold — a
                                         # confirmed blockage; escalates on telemetry alone
    HARD_DECEL = "hard_decel"            # abrupt speed loss beyond racing envelope
    YAW_SPIKE = "yaw_spike"              # rotation spike (spin / snap)
    # Video-derived
    DEBRIS = "debris"                    # object(s) on the racing surface
    SMOKE_OR_DUST = "smoke_or_dust"      # plume — off-track excursion / contact
    STATIONARY_CAR_VISUAL = "stationary_car_visual"  # car not moving, seen on camera
    CONTACT = "contact"                  # visible car-to-car or car-to-wall contact


class TrackLocation(BaseModel):
    """Where an observation places the incident, as available per modality.

    Telemetry has precise GPS; video has a camera id (+ optionally a turn).
    Either may be absent — the Correlator uses whatever it's given.
    """
    turn: Optional[str] = None           # e.g. "T3" — from race-control / circuit plan
    gps_lat: Optional[float] = None
    gps_lng: Optional[float] = None
    camera_id: Optional[str] = None      # CCTV source, video modality


class Observation(BaseModel):
    """One observer's report of something notable at a moment in time.

    Both observers emit these; the Correlator consumes them. `confidence` and
    `severity_hint` are the observer's own read — the Correlator makes the final
    severity call after fusing modalities.
    """
    modality: Modality
    signal: SignalType
    ts_utc: datetime                     # absolute time the observer places it
    car_number: Optional[int] = None     # known for telemetry; sometimes for video
    confidence: float = Field(ge=0.0, le=1.0)
    severity_hint: int = Field(ge=0, le=100, default=0)
    location: TrackLocation = Field(default_factory=TrackLocation)
    summary: str = ""                    # human-readable one-liner
    evidence: dict[str, Any] = Field(default_factory=dict)  # signal-specific payload


# ----------------------------------------------------------------------------
# Correlated incident + report — what the supervisor produces
# ----------------------------------------------------------------------------

class FlagType(str, Enum):
    """The deployment the Correlator can recommend to Race Control."""
    NONE = "none"
    YELLOW = "yellow"
    DOUBLE_YELLOW = "double_yellow"
    SAFETY_CAR = "safety_car"
    RED = "red"


class FlagRecommendation(BaseModel):
    """The one-click action queued for a human official."""
    flag: FlagType
    turns: list[str] = Field(default_factory=list)   # affected turns, if known
    rationale: str = ""


class CorrelatedIncident(BaseModel):
    """One real-world incident, assembled from one or more Observations.

    `corroborated` is True when >1 modality fired inside the tolerance window —
    the high-confidence case Race Control cares most about.
    """
    incident_id: str
    ts_utc: datetime                     # canonical time (earliest contributing obs)
    car_numbers: list[int] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    corroborated: bool = False
    severity: int = Field(ge=0, le=100, default=0)
    location: TrackLocation = Field(default_factory=TrackLocation)


class IncidentReport(BaseModel):
    """The preliminary report drafted for one-click human approval.

    This is the deliverable the Race Control console renders: what happened,
    when, how bad, and the recommended action awaiting an official's click.
    """
    incident: CorrelatedIncident
    headline: str
    narrative: str                       # the drafted preliminary report prose
    recommendation: FlagRecommendation
    drafted_ts_utc: datetime
    approved: Optional[bool] = None       # set by the human at the console
