"""Race Control Reporter — drafts the IncidentReport for one-click approval.

Deterministic parts (facts, flag recommendation) come from fusion.py; this module
assembles them into an IncidentReport and writes the narrative. The narrative is
the one place the model chooses words:

  - draft_report(incident)        → template narrative, PURE + offline (tests, CI)
  - draft_report(incident, llm=True) → Gemini-drafted narrative, runs in Cloud Shell

Both return the same IncidentReport shape, so the console and the grader don't
care which drafted it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from shared.models import CorrelatedIncident, IncidentReport
from correlator import prompts
from correlator.fusion import recommend_flag


def _location_str(incident: CorrelatedIncident) -> str:
    loc = incident.location
    bits = []
    if loc.turn:
        bits.append(loc.turn)
    if loc.gps_lat is not None:
        bits.append(f"GPS {loc.gps_lat:.5f},{loc.gps_lng:.5f}")
    if loc.camera_id:
        bits.append(f"cam {loc.camera_id}")
    return "; ".join(bits) or "unknown"


def _headline(incident: CorrelatedIncident, flag) -> str:
    cars = "/".join(f"#{c}" for c in incident.car_numbers) if incident.car_numbers else "incident"
    where = f" at {incident.location.turn}" if incident.location.turn else ""
    tag = "CORROBORATED" if incident.corroborated else "single-source"
    return f"[{tag}] {cars}{where} — sev {incident.severity} — recommend {flag.flag.value.upper()}"


def _template_narrative(incident: CorrelatedIncident, flag) -> str:
    """Deterministic prose — no model needed. Used for offline tests."""
    cars = ", ".join(f"#{c}" for c in incident.car_numbers) if incident.car_numbers else "one or more cars"
    what = "; ".join(o.summary for o in incident.observations if o.summary)
    corr = (" Telemetry and video observers agree, raising confidence."
            if incident.corroborated else "")
    return (
        f"At {incident.ts_utc:%H:%M:%S} UTC an incident involving {cars} was "
        f"detected at {_location_str(incident)}. Observers reported: {what}."
        f"{corr} Severity assessed at {incident.severity}/100. "
        f"Recommended action: {flag.flag.value.replace('_', ' ')} — {flag.rationale}"
    )


def _clean_narrative(text: str) -> str:
    """Scrub model artifacts out of the drafted prose before it goes on the board.

    Gemini occasionally leaks markdown into an otherwise clean paragraph — a code
    fence, or a dangling emphasis character (we shipped a report ending "...is
    recommended._"). Race Control shouldn't be reading the model's punctuation
    scraps, so strip them.
    """
    import re
    s = (text or "").strip()
    if s.startswith("```"):                       # ```json / ```text fences
        s = s.strip("`").strip()
        if "\n" in s and " " not in s.split("\n", 1)[0]:
            s = s.split("\n", 1)[1]
    s = re.sub(r"\s+", " ", s).strip()            # one clean paragraph
    s = re.sub(r"^[_*`\s]+", "", s)               # leading emphasis junk
    s = re.sub(r"[_*`\s]+$", "", s)               # trailing emphasis junk (the "._")
    return s.strip()


def _llm_narrative(incident: CorrelatedIncident, flag, model: str | None) -> str:
    """Gemini-drafted prose. Runs where Vertex/Gemini creds exist (Cloud Shell)."""
    import os
    from shared.gemini import make_client, retry_call

    model = model or os.environ.get("FE_REPORT_MODEL") or "gemini-3.5-flash"
    facts = prompts.facts_block(
        incident_id=incident.incident_id,
        ts_utc=f"{incident.ts_utc:%Y-%m-%d %H:%M:%S}",
        car_numbers=incident.car_numbers,
        location=_location_str(incident),
        severity=incident.severity,
        corroborated=incident.corroborated,
        observations=[f"[{o.modality.value}] {o.summary}" for o in incident.observations],
        flag=flag.flag.value,
        flag_rationale=flag.rationale,
    )
    client = make_client()
    resp = retry_call(lambda: client.models.generate_content(
        model=model,
        contents=facts,
        config={"system_instruction": prompts.SYSTEM_INSTRUCTION, "temperature": 0.2},
    ), what="report")
    return _clean_narrative(resp.text)


def draft_report(
    incident: CorrelatedIncident,
    *,
    llm: bool = False,
    model: str | None = None,
) -> IncidentReport:
    """Produce the preliminary IncidentReport for `incident`.

    flag recommendation is always deterministic (fusion.recommend_flag). The
    narrative is templated unless llm=True, in which case Gemini drafts it.
    """
    flag = recommend_flag(incident)
    narrative = (
        _llm_narrative(incident, flag, model) if llm
        else _template_narrative(incident, flag)
    )
    return IncidentReport(
        incident=incident,
        headline=_headline(incident, flag),
        narrative=narrative,
        recommendation=flag,
        drafted_ts_utc=datetime.now(timezone.utc),
    )
