# Bonus Board — Challenge 3

The board's lit and Günther reaches Safety Car. Everything here is **additive**: nothing
on this board can break the build you already demo. Do them in any order; each is a real
lesson, not busywork.

Sizing:  **[S]** < 20 min  ·  **[M]** 30–45 min  ·  **[L]** the rest of the afternoon.

Each ticket: **Surface** (what you touch) · **Spec** (what to do — no solution) · **Payoff**
(the beat you get). No solutions here; the complete reference is `solution/`.

---

## VERIFIER

### [S] 1 — Make the sweep concurrent
**Surface:** `_sweep()` in `starter/video_verifier/verifier.py`.
**Spec:** the given sweep runs the six groups one at a time (~60s). Those calls are
independent and I/O-bound. Fan them out with `asyncio.gather` (mind that a failing group
must not sink the sweep — you still want its error recorded).
**Payoff:** ~60s collapses to ~10s. You *feel* it. The lesson: for I/O-bound fan-out,
concurrency is nearly free and the single biggest latency win you'll get.

### [M] 2 — Name the car by livery (and don't lie)
**Surface:** `_prompt(...)`; the module-level `LIVERIES` table is already there.
**Spec:** feed the model a `car# → team livery` hint so it can cross-check the stopped
car by colour **and** number. **Add the guard:** *"if you cannot clearly read a number,
do NOT invent one — just describe the colour."*
**Payoff:** the Video Agent can say "dark blue Maserati, #7" instead of just "a car."
**Gotcha:** without the guard, we watched it confidently invent car numbers. A hint that
invites a lie is worse than no hint. The safety verdict stays about the *track*, not the
identity — the livery only enriches the description.

### [S] 4 — The cleared verdict (the false-alarm veto)
**Surface:** `_aggregate(...)`, plus a look at `correlator/fusion.py::recommend_flag`.
**Spec:** return `state="cleared"` when no group sees a blockage but one sees the car
recover / the line clear. Watch fusion stand the flag down on it.
**Payoff:** run the Evans case (race-second 1373) — a big moment that *drives away* — and
see the system correctly NOT throw a Safety Car. A recommender that cries wolf gets
ignored. **Note the exception:** a telemetry `PROLONGED_STOP` is too strong to veto on a
camera's say-so; fusion keeps persistence authoritative there. Ask yourself why.

### [S] 5 — Honest error surfacing
**Surface:** `_aggregate(...)`.
**Spec:** distinguish **`unseen`** (every group ran and saw nothing — a real all-clear)
from **`error`** (no group even ran — auth/provisioning/network). Surface `error` with
the first error string.
**Payoff:** point the verifier at a bad bucket and watch the console say *"UNAVAILABLE —
video check couldn't run,"* not *"no CCTV view."* An outage that reads as an all-clear is
the most dangerous bug a safety system can have. Make failure loud.

---

## FUSION & DESIGN

### [M] 3 — Graceful degradation (the best argument in the hack)
**Surface:** `correlator/fusion.py` (the flag policy).
**Spec:** you just made the Safety Car depend on a camera. What if the camera *can't* see
the incident (`unseen`) but telemetry reports a **prolonged** stop? Should the system be
paralysed? Implement: `unseen` + a prolonged telemetry stop → escalate anyway.
**Payoff:** the design discussion of the day. There's a real tension between "don't act
without corroboration" and "don't ignore a confirmed stationary car because a camera is
blind." Defend your choice. There isn't one right answer — that's the point.

### [L] 6 — Correlation bonding
**Surface:** `correlator/fusion.py::_bonds` (given, working — read it first).
**Spec:** proximity in time is *not* the same incident. A lone yaw on car #48 must not be
swallowed into car #7's stop 49 seconds later just because it fell inside the window.
Study how `_bonds` keeps same-car threads, genuine co-temporal stops (Fenestraz + Nato),
and cross-modal video reads together while rejecting a stray yaw. Then extend it — e.g.
add a location gate so far-apart stops don't merge.
**Payoff:** subtle, real, and the kind of bug that produces a car implicated in a Safety
Car it had nothing to do with. Correlate on identity and place, not just the clock.

---

## DEPLOY

### [L] 7 — Ship it (the capstone)
**Surface:** `deploy/deploy_correlator.sh`, `correlator/Dockerfile`.
**Spec:** the correlator has run locally all day. Containerise it and deploy it as a Cloud
Run worker pool. Decide which verifier the deployed container runs (`VERIFIER_PACKAGE` —
the image defaults to `solution.video_verifier`; set it to `starter.video_verifier` to
ship *yours*).
**Payoff:** the whole system runs with no laptop in the loop. **Gotcha:** the deployed
container never sources `activate.sh`, so it takes the code default — set the env var at
deploy time if you want your build to fly. This is why the correlator was local all day:
a 5-minute Cloud Build per edit is a brutal inner loop; deploy it once, at the end.
