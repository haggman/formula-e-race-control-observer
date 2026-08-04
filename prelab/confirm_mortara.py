# ============================================================================
# PASTE INTO A NEW CELL (same runtime — reuses ask/NAIVE/PERSISTENCE/_truthy/
# motion_strip from the cells you've already run).
#
# Three questions:
#   A. Is Mortara actually visible at Cam04, and does he stop then leave?
#   B. Is the naive-vs-persistence split at grp_01 @1780 REPEATABLE?
#      (this is the failure the new Task 2 is built on)
#   C. Is the Cam20 "crash" a hallucination? Look at 94, where nothing happened.
# ============================================================================

# ---- A. Mortara at Cam04: stops ~1780, gone by ~1800 ----------------------
motion_strip("grp_01", "BR", [1770, 1782, 1790, 1798, 1815, 1830],
             "MORTARA #48 at Cam04 - stops then DRIVES AWAY?")

# ---- C. Cam20 at race-second 94: is anything there at all? ---------------
motion_strip("grp_05", "BR", [84, 99, 114, 129, 144],
             "Cam20 @94 - model claims a crashed car. Is there one?")


# ---- B. Repeatability of the teaching failure ----------------------------
def ab_repeat(gid_prefix, t, reps=5):
    gid = next(g for g in GROUPS if g.startswith(gid_prefix))
    tally = {"naive": {"blocked": 0, "cleared": 0},
             "persistence": {"blocked": 0, "cleared": 0}}
    print(f"\n=== {gid[:6]} @ {t}s — {reps} runs of each prompt ===")
    for tag, body in (("naive", NAIVE), ("persistence", PERSISTENCE)):
        for i in range(reps):
            d = ask(gid, t, body)
            blk, clr = _truthy(d.get("blockage")), _truthy(d.get("cleared"))
            tally[tag]["blocked"] += blk
            tally[tag]["cleared"] += clr
            print(f"  {tag:12} {i+1}/{reps}  blockage={str(blk):5} cleared={str(clr):5} "
                  f"panel={str(d.get('panel')):5} — {str(d.get('what_you_see',''))[:64]}")
    print(f"  => naive       blocked {tally['naive']['blocked']}/{reps}  "
          f"cleared {tally['naive']['cleared']}/{reps}")
    print(f"  => persistence blocked {tally['persistence']['blocked']}/{reps}  "
          f"cleared {tally['persistence']['cleared']}/{reps}")
    print("  WANT: naive blocked HIGH, persistence blocked LOW "
          "(cleared high is a bonus, not required)")
    return tally

teaching = ab_repeat("grp_01", 1780)          # the proposed Task-2 failure

# ---- and the false positive, quantified ----------------------------------
falsepos = ab_repeat("grp_05", 94)            # nothing is happening here at all
print("\nIf grp_05 @94 comes back blocked repeatedly, that is a confirmed "
      "hallucination, not a flake.")
