# ============================================================================
# PASTE INTO A NEW CELL. Tests HARDENED2 against the same cases.
# v1 failed because it whitelisted the run-off, which is exactly where the
# white-and-blue barrier section at Cam20 sits. v2 discriminates on ARRIVAL:
# a real incident appears during the clip; track furniture is there at second 0.
# ~33 Gemini calls, ~4 min.
# ============================================================================

HARDENED2 = """You are a race-control video verifier deciding whether a SAFETY CAR is
warranted. Telemetry flagged a car that may have stopped on the track. Watch the whole clip
from its first second to its last.

STEP 1 - IS IT ACTUALLY A RACE CAR? A Formula E car is a low open-wheel single-seater with
FOUR VISIBLE WHEELS, a halo hoop over the cockpit, a driver, and team livery and sponsor
decals. The following are NOT race cars, however car-shaped they appear:
- White, or white-and-blue, energy-absorbing barrier sections and barrier stacks. These sit
  angled in run-off areas beside the track, are long and low, often have blue or coloured
  blocks along the bottom edge, and look strikingly like a car lying on its side. They are
  permanent track furniture.
- Advertising hoardings, marshal posts, equipment, tents, trucks, speaker stacks, scaffolding.
- Parked aircraft. This circuit is built on a disused airport and aircraft are parked behind
  the barriers for the entire race.
- Safety cars, course cars, medical cars, recovery vehicles and marshals - UNLESS they are
  attending a stopped race car, in which case the RACE CAR is the blockage.
If you cannot make out wheels and a cockpit, it is not a race car: blockage=false.

STEP 2 - DID IT ARRIVE DURING THIS CLIP? A real incident happens in front of you: the car is
moving, or not yet present, in the FIRST seconds of the clip, and is stationary by the end.
Anything already sitting in the same position in the first seconds and never moving is
permanent track furniture, not an incident, whatever it resembles. Answer blockage=false for
anything present and motionless from the very first second of the clip.

STEP 3 - IS THE RACING LINE STILL BLOCKED AT THE END? Judge the track state at the END of the
clip, not at a single moment:
- A race car that arrived during the clip and is STILL stopped or stranded on or beside the
  racing line at the end: blockage=true, cleared=false.
- A race car that stopped but DROVE AWAY or was recovered, leaving the line clear by the end:
  blockage=false, cleared=true.
- Anything else: blockage=false, cleared=false.

In what_you_see, state (a) whether you can actually see wheels and a cockpit, and (b) whether
the object was already in place in the first seconds of the clip. Report which panel
(TL/TR/BL/BR) it is in, the car number only if clearly legible (else null), and whether other
cars are still moving (feed_live)."""


CASES2 = [
    ("grp_02",  693, "Gunther #7 retires",             "BLOCKED",     "3/3"),
    ("grp_02", 1698, "Fenestraz #23 stranded",         "BLOCKED",     "3/3"),
    ("grp_02", 1780, "#23 still stranded @1780",       "BLOCKED",     "3/3"),
    ("grp_05",   94, "Cam20 @94 - barrier section",    "not blocked", "3/3 FAIL"),
    ("grp_05",  693, "Cam20 @693 - barrier section",   "not blocked", "3/3 FAIL"),
    ("grp_02",   94, "Cam06 @94 - new FP from v1",     "not blocked", "(v1 swept it)"),
    ("grp_04",   94, "Cam15 @94 - new FP from v1",     "not blocked", "(v1 swept it)"),
]

REPS = 3
print(f"\n{'case':32} {'want':12} {'v1 was':>14} {'v2':>6}")
print("-" * 74)
rows = []
for prefix, t, label, want, v1 in CASES2:
    gid = next(g for g in GROUPS if g.startswith(prefix))
    n, sample = 0, ""
    for _ in range(REPS):
        d = ask(gid, t, HARDENED2)
        n += _truthy(d.get("blockage"))
        if not sample:
            sample = str(d.get("what_you_see", ""))[:100]
    flag = ""
    if want == "BLOCKED" and n < REPS:      flag = "  <-- REGRESSION"
    if want == "not blocked" and n > 0:     flag = "  <-- still firing"
    print(f"{label:32} {want:12} {v1:>14} {n:>4}/{REPS}{flag}")
    print(f"    e.g. {sample}")
    rows.append(dict(case=label, want=want, v2=f"{n}/{REPS}"))

print("\nPASS = BLOCKED rows 3/3, 'not blocked' rows 0/3.")


def sweep2(t, label):
    print(f"\n### HARDENED2 SWEEP — {label} (t={t})")
    hits = []
    for gid in GROUPS:
        d = ask(gid, t, HARDENED2)
        if _truthy(d.get("blockage")):
            pan = str(d.get("panel", "none"))
            cam = GROUPS[gid][PANELS.index(pan)] if pan in PANELS else None
            hits.append(cam)
            print(f"  BLOCKED {gid[:6]} cam={cam} — {str(d.get('what_you_see',''))[:90]}")
    print(f"  => {'blocked ' + str(hits) if hits else 'NOT BLOCKED (clean)'}")

sweep2(94,  "pit stop — MUST come back clean")
sweep2(693, "Gunther — MUST be Cam05 and nothing else")
