"""Correlator fusion — the deterministic core of the supervising agent.

PURE AND DETERMINISTIC: no I/O, no clocks, no model calls. Takes the Observations
that the two observers reported and assembles them into CorrelatedIncidents, then
recommends a flag. The reporter (correlator/reporter.py) drafts the human-readable
narrative on top — that's the one place the model chooses words.

Why fusion is the heart of the hack: the two observers see the SAME incident
through different senses and DON'T share a clock exactly (telemetry stop vs. video
frame can differ by seconds — we measured ~17s between telemetry and the race
control message). So we correlate within a TOLERANCE WINDOW, never by exact
timestamp, and we treat agreement across modalities as the high-confidence signal
Race Control cares most about.

The flag recommendation is deliberately DETERMINISTIC (a policy table), not left
to the model: a safety call should be explainable and repeatable. The model
narrates; the policy decides.
"""
from __future__ import annotations

from datetime import timedelta
from itertools import count

from shared.models import (
    CorrelatedIncident,
    FlagRecommendation,
    FlagType,
    Modality,
    Observation,
    SignalType,
    TrackLocation,
)

# ============================================================================
# Tunable knobs
# ============================================================================

# Time tolerance for grouping observations into one incident. Wide on purpose:
# the two sensors detect the SAME incident at very different latencies. Telemetry
# fires the instant speed→0 (~6s); the video observer only flags the visible
# AFTERMATH (a stopped car with marshals), which can lag the stop by up to ~90s.
# The window has to span that gap or the two never meet and nothing corroborates.
# (Trade-off: in a dense-incident scenario this could merge two distinct incidents
# that happen within the window — a location/car-aware correlator is the fuller
# fix; for the jump-to-one-incident demo this wide window is correct.)
CORRELATION_WINDOW_S = 120.0

# Corroboration boost: when >1 modality agrees inside the window, the incident is
# more certain and more severe than either observer alone claimed.
CORROBORATION_BOOST = 15

# Signals that mean "a car is not moving where it should be" — the strongest
# cross-modal anchor (telemetry sees speed→0, video sees it sitting there).
_STOPPED_SIGNALS = {
    SignalType.STOPPED_CAR,
    SignalType.PROLONGED_STOP,
    SignalType.STATIONARY_CAR_VISUAL,
}


# ============================================================================
# Fusion
# ============================================================================

def correlate(
    observations: list[Observation],
    *,
    window_s: float = CORRELATION_WINDOW_S,
    race_id: str = "berlin_2024_r10",
) -> list[CorrelatedIncident]:
    """Group Observations into CorrelatedIncidents by time proximity.

    Observations within `window_s` of a growing cluster's span join it. Cars and
    locations merge; an incident is `corroborated` when it carries >1 modality.
    Returns incidents in time order.
    """
    if not observations:
        return []

    obs = sorted(observations, key=lambda o: o.ts_utc)
    ids = (f"{race_id}_inc{n:02d}" for n in count(1))

    clusters: list[list[Observation]] = [[obs[0]]]
    for o in obs[1:]:
        last = clusters[-1]
        # join if within the window of the cluster's most recent observation
        if (o.ts_utc - last[-1].ts_utc) <= timedelta(seconds=window_s):
            last.append(o)
        else:
            clusters.append([o])

    return [_assemble(c, next(ids)) for c in clusters]


def _assemble(cluster: list[Observation], incident_id: str) -> CorrelatedIncident:
    """Fold one cluster of Observations into a CorrelatedIncident."""
    modalities = {o.modality for o in cluster}
    corroborated = len(modalities) > 1

    cars: list[int] = []
    for o in cluster:
        if o.car_number is not None and o.car_number not in cars:
            cars.append(o.car_number)

    severity = _severity(cluster, corroborated)

    return CorrelatedIncident(
        incident_id=incident_id,
        ts_utc=cluster[0].ts_utc,           # earliest contributing observation
        car_numbers=cars,
        observations=list(cluster),
        corroborated=corroborated,
        severity=severity,
        location=_merge_location(cluster),
    )


def _severity(cluster: list[Observation], corroborated: bool) -> int:
    """Confidence-weighted peak severity, boosted when modalities agree.

    Base = the highest severity_hint any observer gave, gently discounted by that
    observer's confidence. Corroboration across senses adds a boost. Multiple
    stopped cars in one place add a little more (a bigger blockage).
    """
    if not cluster:
        return 0
    base = max(int(o.severity_hint * (0.6 + 0.4 * o.confidence)) for o in cluster)
    if corroborated:
        base += CORROBORATION_BOOST
    stopped_cars = {
        o.car_number for o in cluster
        if o.signal in _STOPPED_SIGNALS and o.car_number is not None
    }
    if len(stopped_cars) >= 2:
        base += 10
    return max(0, min(100, base))


def _merge_location(cluster: list[Observation]) -> TrackLocation:
    """Prefer precise telemetry GPS; keep the video camera id and any turn."""
    merged = TrackLocation()
    for o in cluster:
        loc = o.location
        if loc.gps_lat is not None and merged.gps_lat is None:
            merged.gps_lat, merged.gps_lng = loc.gps_lat, loc.gps_lng
        if loc.camera_id and not merged.camera_id:
            merged.camera_id = loc.camera_id
        if loc.turn and not merged.turn:
            merged.turn = loc.turn
    return merged


# ============================================================================
# Flag policy — deterministic, explainable, repeatable
# ============================================================================

def recommend_flag(incident: CorrelatedIncident) -> FlagRecommendation:
    """Map a correlated incident to a recommended deployment.

    CORROBORATION is the escalator — that is the whole point of fusing two
    observers. A telemetry stop ALONE is ambiguous: a car sitting still might be
    a genuine track blockage or just a pit stop (GPS can't always tell, and #2
    Vandoorne's R10 front-wing stop is exactly this trap). So a single-sensor
    stop only earns a double yellow "pending confirmation"; it takes VIDEO
    agreement (the camera sees it on the racing surface) to justify a full
    Safety Car. This mirrors how R10 was officiated — the Fenestraz/Nato stop,
    unmistakable on camera, drew the Safety Car.

    Policy:
      - stopped car confirmed by BOTH senses, or >=2 cars stopped, or sev >= 85
                                                     → SAFETY_CAR
      - single-sensor stop, severity 60-79, or debris → DOUBLE_YELLOW
      - severity 40-59                                → YELLOW at the turn
      - below that                                    → NONE (note / keep watching)
    """
    turns = [incident.location.turn] if incident.location.turn else []
    stopped = [
        o for o in incident.observations if o.signal in _STOPPED_SIGNALS
    ]
    stopped_cars = {o.car_number for o in stopped if o.car_number is not None}
    confirmed_stop = stopped and incident.corroborated
    # A telemetry PROLONGED_STOP is a confirmed blockage on its own — escalate
    # fast, without waiting for the (slow, variable) video corroboration.
    prolonged = any(o.signal == SignalType.PROLONGED_STOP for o in incident.observations)

    if len(stopped_cars) >= 2 or confirmed_stop or prolonged or incident.severity >= 85:
        return FlagRecommendation(
            flag=FlagType.SAFETY_CAR, turns=turns,
            rationale=_stopped_rationale(stopped_cars, incident),
        )
    if stopped:
        return FlagRecommendation(
            flag=FlagType.DOUBLE_YELLOW, turns=turns,
            rationale=(f"Single-sensor stop (car "
                       f"{', '.join('#'+str(c) for c in sorted(stopped_cars))}); "
                       "marshals out, pending video confirmation before Safety Car."),
        )
    if incident.severity >= 60 or _has(incident, SignalType.DEBRIS):
        return FlagRecommendation(
            flag=FlagType.DOUBLE_YELLOW, turns=turns,
            rationale="Hazard on or beside the racing line; marshals required.",
        )
    if incident.severity >= 40:
        return FlagRecommendation(
            flag=FlagType.YELLOW, turns=turns,
            rationale="Localised incident; caution through the sector.",
        )
    return FlagRecommendation(
        flag=FlagType.NONE, turns=turns,
        rationale="Below flag threshold; note and keep watching.",
    )


def _stopped_rationale(stopped_cars: set[int], incident: CorrelatedIncident) -> str:
    who = ", ".join(f"#{c}" for c in sorted(stopped_cars)) or "a car"
    where = f" at {incident.location.turn}" if incident.location.turn else ""
    corr = " Confirmed by both telemetry and video." if incident.corroborated else ""
    return f"Stopped car(s) {who}{where} — track obstruction.{corr}"


def _has(incident: CorrelatedIncident, signal: SignalType) -> bool:
    return any(o.signal == signal for o in incident.observations)
