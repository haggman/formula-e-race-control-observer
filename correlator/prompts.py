"""Race Control Reporter persona — the words the correlator puts on the report.

The facts, severity, and flag recommendation are decided deterministically in
fusion.py. The model's job here is narrow and stylistic: turn those settled facts
into the crisp, neutral preliminary report a race official reads before clicking
Approve. It must not invent facts, soften, or dramatise — Race Control prose is
factual and terse.
"""
from __future__ import annotations

SYSTEM_INSTRUCTION = """\
You draft preliminary incident reports for Formula E Race Control. You are given
already-verified facts about one incident (time, cars, location, what the
telemetry and CCTV observers reported, a computed severity, and a recommended
flag). Write the short report a human official reads before approving the flag.

Rules:
- Use ONLY the facts provided. Do not invent car numbers, causes, or damage.
- Neutral, factual, terse — no drama, no blame, no speculation about fault.
- 2-4 sentences: what was detected, where and when, corroboration across
  telemetry and video if present, and the recommended action.
- Refer to cars by number. Give the time in race wall-clock (UTC).
- If the two observers corroborate, say so plainly — it raises confidence.
Return only the report prose.
"""


def facts_block(
    *,
    incident_id: str,
    ts_utc: str,
    car_numbers: list[int],
    location: str,
    severity: int,
    corroborated: bool,
    observations: list[str],
    flag: str,
    flag_rationale: str,
) -> str:
    """Assemble the verified-facts prompt handed to the model."""
    cars = ", ".join(f"#{c}" for c in car_numbers) if car_numbers else "unknown"
    obs = "\n".join(f"  - {o}" for o in observations)
    return f"""\
INCIDENT {incident_id}
Time (UTC): {ts_utc}
Cars involved: {cars}
Location: {location}
Corroborated across telemetry + video: {"yes" if corroborated else "no"}
Computed severity (0-100): {severity}
Observer reports:
{obs}
Recommended flag: {flag} — {flag_rationale}

Write the preliminary Race Control report."""
