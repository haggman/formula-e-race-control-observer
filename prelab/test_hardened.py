# ============================================================================
# PASTE INTO A NEW CELL (same runtime).
#   PART 1: zoom Cam20 so we can see what the model keeps calling a crash.
#   PART 2: A/B the CURRENT persistence prompt against a HARDENED one.
# ~55 Gemini calls, ~6 min.
# ============================================================================

# ---------- PART 1: what is actually in Cam20? (no model calls) ------------
def zoom_panel(gid_prefix, panel, offset, right_half=False):
    gid = next(g for g in GROUPS if g.startswith(gid_prefix))
    crop = panel_crop(gid, panel, offset)
    if right_half:
        crop = crop[:, crop.shape[1] // 2:]
    plt.figure(figsize=(20, 13))
    plt.imshow(crop); plt.axis("off")
    plt.title(f"{gid} {panel}={GROUPS[gid][PANELS.index(panel)]} @ offset {offset}"
              f"{' (right half)' if right_half else ''}", fontsize=15)
    plt.show()

zoom_panel("grp_05", "BR", 114)                  # full panel, big
zoom_panel("grp_05", "BR", 114, right_half=True) # the "right side of the track"
zoom_panel("grp_01", "BL", 114)                  # Cam03 - the aircraft I think I saw


# ---------- PART 2: the hardened prompt ------------------------------------
HARDENED = """You are a race-control video verifier deciding whether a SAFETY CAR is
warranted. Telemetry flagged a car that may be stopped on the track. Watch the whole clip.

WHAT COUNTS AS A BLOCKAGE. Only a Formula E race car - an open-wheel single-seater -
and only if it is ON the racing surface or immediately beside it, INSIDE the barriers
and walls that line the circuit.

WHAT DOES NOT COUNT, however stationary it looks:
- Anything beyond the barrier or in the background. This circuit is built on a disused
  airport: parked aircraft, service vehicles, trucks, tents, marshal posts, equipment
  and buildings are permanently in shot behind the barriers for the whole race. They
  are scenery, not incidents.
- Safety cars, course cars, medical cars, recovery vehicles and marshals - unless they
  are attending a stopped race car, in which case the race car is the blockage.
- Any car in the pit lane or pit boxes.
If the only stationary thing you can see is one of the above, answer blockage=false
and cleared=false.

WHEN IT IS BLOCKED. Judge the TRACK STATE at the END of the clip, not at one moment:
- A race car STILL stopped or stranded on or beside the racing line at the end
  (a persistent obstruction, perhaps with marshals or a recovery vehicle):
  blockage=true, cleared=false.
- A race car appeared but DROVE AWAY or was recovered, so the line is clear by the end:
  blockage=false, cleared=true.
- No stopped race car at any point: blockage=false, cleared=false.

In what_you_see, state WHERE the object is relative to the barrier - "on the racing
surface", "in the run-off inside the barrier", or "behind the barrier". Report which
panel (TL/TR/BL/BR) it is in, the car number only if clearly legible (else null), and
whether other cars are still moving (feed_live)."""


CASES = [
    # (group prefix, race-second, label, what a CORRECT verifier should say)
    ("grp_02",  693, "Gunther #7 retires",              "BLOCKED"),
    ("grp_05",   94, "Cam20 @94 - nothing happened",    "not blocked"),
    ("grp_05",  693, "Cam20 @693 - false positive",     "not blocked"),
    ("grp_01",  693, "Cam04 @693 - false positive",     "not blocked"),
    ("grp_03",  693, "Cam10 @693 - false positive",     "not blocked"),
    ("grp_02", 1698, "Fenestraz #23 stranded",          "BLOCKED"),
    ("grp_02", 1780, "#23 still stranded @1780",        "BLOCKED"),
]

REPS = 3
print(f"\n{'case':34} {'want':12} {'current':>9} {'hardened':>9}")
print("-" * 78)
ab = []
for prefix, t, label, want in CASES:
    gid = next(g for g in GROUPS if g.startswith(prefix))
    got = {}
    for tag, body in (("current", PERSISTENCE), ("hardened", HARDENED)):
        n = 0
        for _ in range(REPS):
            d = ask(gid, t, body)
            n += _truthy(d.get("blockage"))
        got[tag] = n
    ab.append(dict(case=label, group=gid, t=t, want=want,
                   current=f"{got['current']}/{REPS}", hardened=f"{got['hardened']}/{REPS}"))
    flag = ""
    if want == "BLOCKED" and got["hardened"] < REPS:          flag = "  <-- REGRESSION"
    if want == "not blocked" and got["hardened"] > 0:         flag = "  <-- still firing"
    print(f"{label:34} {want:12} {got['current']:>7}/{REPS} {got['hardened']:>7}/{REPS}{flag}")

print("\nPASS = every BLOCKED row is 3/3 hardened, every 'not blocked' row is 0/3 hardened.")

# ---------- full sweep with the hardened prompt ----------------------------
def hardened_sweep(t, label):
    print(f"\n### HARDENED SWEEP — {label} (t={t})")
    blocked = []
    for gid in GROUPS:
        d = ask(gid, t, HARDENED)
        if _truthy(d.get("blockage")):
            pan = str(d.get("panel", "none"))
            cam = GROUPS[gid][PANELS.index(pan)] if pan in PANELS else None
            blocked.append(cam)
            print(f"  BLOCKED {gid[:6]} cam={cam} — {str(d.get('what_you_see',''))[:78]}")
    print(f"  => {'blocked ' + str(blocked) if blocked else 'NOT BLOCKED (clean)'}")

hardened_sweep(94,  "pit stop — MUST come back clean")
hardened_sweep(693, "Gunther — must find Cam05 and nothing else")
