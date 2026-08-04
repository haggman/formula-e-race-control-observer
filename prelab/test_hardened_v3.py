# ============================================================================
# PASTE INTO A NEW CELL. FINAL prompt attempt.
#
# v1 failed: whitelisted the run-off, where the Cam20 barrier lives.
# v2 failed: "must have arrived during the clip" -> model CONFABULATED the
#            arrival ("at 02:16 a car crashes"), and the rule also excluded
#            genuine ONGOING blockages (#23 @1780 -> 0/3).
# v3 changes ONE mechanism: it removes the PRIMING. Every previous version
#    opened with "telemetry flagged a car that may be stopped" - telling the
#    model an incident is expected before it looks. v3 says the opposite, and
#    explicitly forbids narrating an impact it did not observe.
#    Keeps END-of-window (fixes the @1780 regression). Drops the arrival rule.
# ~33 calls, ~4 min.
# ============================================================================

V3 = """You are reviewing a routine CCTV clip from a Formula E circuit. Nothing in
particular is expected to have happened. MOST clips show ordinary racing, and for most
clips the correct answer is that nothing is blocked. Do not go looking for an incident.

Your question: at the END of this clip, is a Formula E RACE CAR sitting stationary on the
racing surface or its immediate run-off?

A Formula E race car is a low open-wheel single-seater with FOUR VISIBLE WHEELS, a halo
hoop over an open cockpit, a driver, and team livery. Before you answer yes, check all of:

- Can you actually resolve wheels and a cockpit on the object? White, or white-and-blue,
  energy-absorbing barrier sections lie angled in the run-off beside this track. They are
  long, low, have coloured blocks along the bottom edge, and closely resemble a car lying
  on its side. Advertising hoardings, marshal posts, equipment, trucks and parked aircraft
  are permanent fixtures here - this circuit is built on a disused airport. None of these
  are race cars.
- Is it a race car rather than a safety car, course car, medical car or recovery vehicle?
  Those may be present legitimately. If a recovery vehicle is attending a stopped RACE CAR,
  the race car is the blockage.
- Is it on the track, rather than in the pit lane or a pit box?

Report only what you actually observed. Do NOT describe a crash, a collision or an impact
unless you watched it happen in this clip. If an object is simply present and still, say
that it is present and still - do not narrate how it got there, and do not invent a time
for an event you did not see.

Answers:
- blockage=true, cleared=false - a race car is stationary on or beside the racing line at
  the END of the clip, whether it stopped during this clip or was already stopped when the
  clip began.
- blockage=false, cleared=true - a race car was stationary but has driven away or been
  recovered, and the line is clear by the end.
- blockage=false, cleared=false - anything else, including when the only still objects are
  barriers, hoardings, equipment, aircraft or service vehicles.

In what_you_see, state plainly whether you can resolve wheels and a cockpit. Report which
panel (TL/TR/BL/BR), the car number only if clearly legible (else null), and whether other
cars are still moving (feed_live)."""


CASES3 = [
    ("grp_02",  693, "Gunther #7 retires",           "BLOCKED",     "v1 3/3  v2 3/3"),
    ("grp_02", 1698, "Fenestraz #23 stranded",       "BLOCKED",     "v1 3/3  v2 3/3"),
    ("grp_02", 1780, "#23 ONGOING blockage @1780",   "BLOCKED",     "v1 3/3  v2 0/3"),
    ("grp_05",   94, "Cam20 @94 - barrier section",  "not blocked", "v1 3/3  v2 3/3"),
    ("grp_05",  693, "Cam20 @693 - barrier section", "not blocked", "v1 3/3  v2 3/3"),
    ("grp_02",   94, "Cam06 @94",                    "not blocked", "v2 1/3"),
    ("grp_04",   94, "Cam15 @94",                    "not blocked", "v2 1/3"),
]

REPS = 3
print(f"\n{'case':32} {'want':12} {'history':>16} {'v3':>6}")
print("-" * 78)
verdicts = {}
for prefix, t, label, want, hist in CASES3:
    gid = next(g for g in GROUPS if g.startswith(prefix))
    n, sample = 0, ""
    for _ in range(REPS):
        d = ask(gid, t, V3)
        n += _truthy(d.get("blockage"))
        if not sample:
            sample = str(d.get("what_you_see", ""))[:110]
    flag = ""
    if want == "BLOCKED" and n < REPS:   flag = "  <-- REGRESSION"
    if want == "not blocked" and n > 0:  flag = "  <-- still firing"
    verdicts[label] = n
    print(f"{label:32} {want:12} {hist:>16} {n:>4}/{REPS}{flag}")
    print(f"    e.g. {sample}")

print("\nPASS = BLOCKED rows 3/3, 'not blocked' rows 0/3.")


def sweep3(t, label):
    print(f"\n### V3 SWEEP — {label} (t={t})")
    hits = []
    for gid in GROUPS:
        d = ask(gid, t, V3)
        if _truthy(d.get("blockage")):
            pan = str(d.get("panel", "none"))
            cam = GROUPS[gid][PANELS.index(pan)] if pan in PANELS else None
            hits.append(cam)
            print(f"  BLOCKED {gid[:6]} cam={cam} — {str(d.get('what_you_see',''))[:90]}")
    print(f"  => {'blocked ' + str(hits) if hits else 'NOT BLOCKED (clean)'}")

sweep3(94,  "pit stop — MUST come back clean")
sweep3(693, "Gunther — MUST be Cam05 and nothing else")

print("\n" + "=" * 70)
print("STOPPING RULE: if 'Cam20 @94' is not 0/3, we stop tuning the prompt and")
print("ship as-is, documenting the false positive as Task 2's teaching material.")
print("=" * 70)
