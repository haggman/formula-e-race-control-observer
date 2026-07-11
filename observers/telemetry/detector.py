"""Telemetry incident detector — the deterministic "when" for Observer 2.

PURE AND DETERMINISTIC: no I/O, no clocks, no randomness. Same window in →
same Observations out. A runner (scripts/probe_telemetry.py now, the streaming
service later) owns polling and debounce; this module only decides whether a
window of samples looks like an incident.

This is the safety analog of shared/scorer.py in the other two hacks: the code
decides WHEN something happened; the agent later decides WHAT it was.

Design — three signals, in order of reliability:
  1. STOPPED_CAR (primary). Speed held near zero for >= STOP_HOLD_S. This is the
     unambiguous one: cars don't sit still on a race track on purpose. It maps
     directly to what the video observer sees (a car stationary on track), which
     is why it anchors cross-modal correlation. Retirements light this up hard.
  2. HARD_DECEL (secondary). A speed drop far beyond the racing envelope. Berlin
     brakes 230→50 into the hairpin every lap, so the threshold sits ABOVE normal
     hard braking — we want "hit something", not "braked well".
  3. YAW_SPIKE (secondary). |yaw_rate| far above cornering, especially while
     also losing speed — the signature of a snap/spin.

Only STOPPED_CAR fires as a high-confidence trigger on its own. HARD_DECEL and
YAW_SPIKE are lower-confidence hints that raise severity and give the correlator
something to fuse with the video feed; on their own they stay advisory.
"""
from __future__ import annotations

from statistics import mean
from typing import Optional

from shared.models import (
    Modality,
    Observation,
    SignalType,
    TelemetrySample,
    TrackLocation,
)

# ============================================================================
# Tunable thresholds (dial these against laps of real telemetry)
# ============================================================================

# --- Stopped car (primary) ---
STOP_SPEED_KMH = 5.0        # at/below this we consider the car not moving
STOP_HOLD_S = 6.0           # must hold that slow for this long to count
STOP_CONF = 0.97            # near-certain: a stationary car on a live track
STOP_ESCALATE_S = 18.0      # still stopped this long → PROLONGED_STOP (→ Safety Car
                            # on telemetry alone, without waiting on video)
PROLONGED_CONF = 0.98
SEV_PROLONGED = 92

# --- Pit-lane guard ---
# A car sitting in its PIT BOX for 18s is a normal (long) pit stop, NOT a track
# blockage — it must not trigger a Safety Car. Approximate box derived from
# Vandoorne #2's confirmed R10 front-wing pit stop (52.48012, 13.39256); the four
# real track incidents fall clearly outside it. FIRST APPROXIMATION — refine
# against the official pit geometry (reference/cctv_tsbs_plan.pdf / circuit plan).
PIT_LANE_BOX = {"lat_min": 52.4797, "lat_max": 52.4806,
                "lng_min": 13.3916, "lng_max": 13.3938}


def in_pit_lane(lat: float, lng: float) -> bool:
    """True if a GPS point is inside the (approximate) pit-lane box."""
    b = PIT_LANE_BOX
    return (b["lat_min"] <= lat <= b["lat_max"] and
            b["lng_min"] <= lng <= b["lng_max"])

# --- Hard deceleration (secondary) ---
# Measured envelope (Berlin R10, clean car): normal 1.5s speed drops peak at
# ~68 km/h (p99), but legitimate corner braking ALWAYS exits at speed (~100 km/h
# median) — the car keeps going. An impact/off brings the car in fast and leaves
# it slow. So we gate on all three: fast entry, big drop, AND a slow exit. That
# discriminates "hit something" from "braked well for the hairpin".
DECEL_DROP_KMH = 80.0        # speed lost within the decel window
DECEL_WINDOW_S = 1.5         # ...measured over this short a window
DECEL_MIN_ENTRY_KMH = 120.0  # must have come in fast (not pit/hairpin crawl)
DECEL_EXIT_MAX_KMH = 50.0    # ...and ended up slow (not just corner braking)
DECEL_CONF = 0.6

# --- Yaw spike (secondary) ---
# Normal Berlin cornering yaw sits within ~+/-15 deg/s. A spin snaps well past.
YAW_ABS = 40.0              # |yaw_rate| beyond this is not normal cornering
YAW_CONF = 0.5

# --- Severity hints (0-100; the correlator makes the final call) ---
SEV_STOPPED = 80
SEV_HARD_DECEL = 55
SEV_YAW = 45


def _loc(sample: TelemetrySample) -> TrackLocation:
    return TrackLocation(gps_lat=sample.lat, gps_lng=sample.lng)


def detect(
    window: list[TelemetrySample],
    *,
    already_stopped: bool = False,
    already_pitted: bool = False,
) -> list[Observation]:
    """Score one time-ordered window of samples for a SINGLE car.

    Args:
      window: samples for one car, sorted by ts_utc, spanning at least the
        longest lookback the rules need (>= STOP_HOLD_S of 20 Hz data).
      already_stopped: caller-tracked latch — True if we've ALREADY emitted a
        STOPPED_CAR for this ongoing stop. Prevents re-firing every window while
        a retired car sits there. The caller flips it back to False once the car
        moves again.

    Returns Observations for whatever fired, best (most severe) first. Empty
    list means "nothing notable this window."
    """
    if len(window) < 2:
        return []

    window = sorted(window, key=lambda s: s.ts_utc)
    car = window[-1].car_number
    out: list[Observation] = []

    # --- 1. Stopped car (primary) --------------------------------------------
    tail = _tail_within(window, STOP_HOLD_S)
    stationary = (_span_s(tail) >= STOP_HOLD_S
                  and all(s.speed_kmh <= STOP_SPEED_KMH for s in tail))
    # A car stopped in its PIT BOX is a pit stop, not a track incident — this is the
    # false-positive trap the whole system exists to avoid (a naive detector throws a
    # Safety Car for a routine stop). We still SAY so, rather than staying silent:
    # an invisible dismissal is indistinguishable from a broken observer.
    in_pit = in_pit_lane(tail[-1].lat, tail[-1].lng) if tail else False

    if stationary and in_pit and not already_pitted:
        out.append(Observation(
            modality=Modality.TELEMETRY,
            signal=SignalType.PIT_STOP,
            ts_utc=tail[0].ts_utc,
            car_number=car,
            confidence=STOP_CONF,
            severity_hint=0,                      # note only — never a flag
            location=_loc(tail[-1]),
            summary=f"car {car} stationary in the pit lane — routine pit stop, "
                    f"not a track incident",
            evidence={"hold_s": round(_span_s(tail), 1), "pit_lane": True},
        ))
    elif stationary and not in_pit and not already_stopped:
        out.append(Observation(
            modality=Modality.TELEMETRY,
            signal=SignalType.STOPPED_CAR,
            ts_utc=tail[0].ts_utc,                # when the stop began
            car_number=car,
            confidence=STOP_CONF,
            severity_hint=SEV_STOPPED,
            location=_loc(tail[-1]),
            summary=f"car {car} stopped on track for {_span_s(tail):.0f}s",
            evidence={
                "hold_s": round(_span_s(tail), 1),
                "mean_speed_kmh": round(mean(s.speed_kmh for s in tail), 1),
            },
        ))

    # --- 2. Hard deceleration (secondary) ------------------------------------
    dec = _hard_decel(window)
    if dec is not None:
        entry, exit_, drop = dec
        out.append(Observation(
            modality=Modality.TELEMETRY,
            signal=SignalType.HARD_DECEL,
            ts_utc=exit_.ts_utc,
            car_number=car,
            confidence=DECEL_CONF,
            severity_hint=SEV_HARD_DECEL,
            location=_loc(exit_),
            summary=(f"car {car} lost {drop:.0f} km/h in "
                     f"{_span_s([entry, exit_]):.1f}s"),
            evidence={
                "entry_kmh": round(entry.speed_kmh, 1),
                "exit_kmh": round(exit_.speed_kmh, 1),
                "drop_kmh": round(drop, 1),
            },
        ))

    # --- 3. Yaw spike (secondary) --------------------------------------------
    peak = max(window, key=lambda s: abs(s.yaw_rate))
    if abs(peak.yaw_rate) >= YAW_ABS:
        out.append(Observation(
            modality=Modality.TELEMETRY,
            signal=SignalType.YAW_SPIKE,
            ts_utc=peak.ts_utc,
            car_number=car,
            confidence=YAW_CONF,
            severity_hint=SEV_YAW,
            location=_loc(peak),
            summary=f"car {car} yaw spike {peak.yaw_rate:+.0f} deg/s",
            evidence={"yaw_rate": round(peak.yaw_rate, 1),
                      "speed_kmh": round(peak.speed_kmh, 1)},
        ))

    out.sort(key=lambda o: o.severity_hint, reverse=True)
    return out


# ============================================================================
# Helpers
# ============================================================================

def _span_s(samples: list[TelemetrySample]) -> float:
    """Wall-clock seconds spanned by a sample list."""
    if len(samples) < 2:
        return 0.0
    return (samples[-1].ts_utc - samples[0].ts_utc).total_seconds()


def _tail_within(window: list[TelemetrySample], seconds: float) -> list[TelemetrySample]:
    """The suffix of `window` covering the last `seconds`."""
    if not window:
        return []
    cutoff = window[-1].ts_utc.timestamp() - seconds
    return [s for s in window if s.ts_utc.timestamp() >= cutoff]


def _hard_decel(
    window: list[TelemetrySample],
) -> Optional[tuple[TelemetrySample, TelemetrySample, float]]:
    """Find the sharpest speed drop over any <= DECEL_WINDOW_S sub-span.

    Returns (entry, exit, drop_kmh) if it clears DECEL_DROP_KMH from an entry
    speed above DECEL_MIN_ENTRY_KMH, else None. O(n) two-pointer over the window.
    """
    best: Optional[tuple[TelemetrySample, TelemetrySample, float]] = None
    i = 0
    for j in range(len(window)):
        while (window[j].ts_utc - window[i].ts_utc).total_seconds() > DECEL_WINDOW_S:
            i += 1
        entry, exit_ = window[i], window[j]
        if entry.speed_kmh < DECEL_MIN_ENTRY_KMH:
            continue
        if exit_.speed_kmh > DECEL_EXIT_MAX_KMH:
            continue                       # exited at speed → corner braking, not impact
        drop = entry.speed_kmh - exit_.speed_kmh
        if drop >= DECEL_DROP_KMH and (best is None or drop > best[2]):
            best = (entry, exit_, drop)
    return best
