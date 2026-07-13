# Demo — Challenge 3: The Proactive Race Control Observer

Event timeline and the minute-by-minute opening live in [`RUN_OF_SHOW.md`](RUN_OF_SHOW.md).
This doc owns the demo **material** — the pit wall, the scripted incidents, the question
bank, and the talking points.

## What you're looking at, in one paragraph

The 2024 Berlin E-Prix (Round 10) replaying at 1 Hz. Two observers watch it — one reads
the cars' telemetry, one reads the trackside CCTV — and a correlator fuses them into a
single recommended flag, drafts a preliminary incident report, and queues it for a human
to approve with one click. The demo build runs the **solution** verifier, so the video
column resolves fast.

## The two senses — the idea to teach

> A car stopped in the pit lane and a car stopped in Turn 3 are **identical in
> telemetry.** Speed is zero in both.

That single fact is the reason the system has two senses. Telemetry raises its hand; the
camera answers *is the racing line actually blocked?* And the flag itself is decided by
deterministic code, not the model — the model narrates, the policy decides.

## Running the demo

```bash
source activate.sh
export VERIFIER_PACKAGE=solution.video_verifier
python -m correlator.service         # leave running; it feeds the console
```

Drive the race from the console's controls (they call the simulator): **jump** to a
race-second, **pause/resume**, **restart**. Every scripted moment below is a jump.

## The pit wall, panel by panel

- **Telemetry Agent** — the deterministic detector's feed: *stopped*, *still stopped
  (confirmed)*, *recovered*, *routine pit stop*. Cheap and instant.
- **Video Agent** — the verifier's feed: *[QUEUED]* (waiting for the window to play),
  *[ANALYZING]*, then a verdict — *CONFIRMED — track blocked, Cam05*, or *CLEARED*, or
  *no CCTV view*. This is the column the students bring to life.
- **Race Control** — the fused recommendation and its rationale, with the **Approve**
  button. This is where two senses become one flag.
- **Agent status** — the heartbeats (telemetry / video / correlator), so you can see
  who's alive.

## The scripted moments (in race order)

Jump to each race-second. Watch the two feeds converge into a recommendation.

1. **94 s — #33 Ticktum, pit lane.** Telemetry sees a stationary car and *dismisses it
   out loud*: "routine pit stop, not a track incident." **No flag.** Open here to prove
   the system is as careful about false alarms as real ones.
2. **693 s — #7 Günther, retires on track (the hero).** Telemetry: *stopped* → *still
   stopped after 18s, confirmed*. Video: *[QUEUED]* until the window plays (~748s), then
   *[ANALYZING]* → **CONFIRMED, track blocked, Cam05** (a dark blue Maserati by the wall).
   Race Control escalates **double yellow → Safety Car · corroborated.** The Approve
   button appears. **This is the money moment.**
3. **1507 s — #2 Vandoorne, pit lane.** Another routine stop, correctly not flagged — a
   second data point that the pit-lane guard isn't a fluke.
4. **1692 s — #23 Fenestraz + #17 Nato, together.** Two cars stop at once — a genuine
   multi-car blockage the correlator fuses into ONE incident. Video confirms → **Safety
   Car.** Then **Nato recovers (~1701)** and drives away: watch the rationale update —
   *"#17 is racing again, but the flag stands while #23 remains stranded."* The board
   stops implicating a car that has already left.
5. **1780 s — #48 Mortara.** A third stop → **Safety Car · corroborated** — proof the arc
   isn't hand-tuned to one incident.

*(Evans #9 has a big moment near 1373 s but never fully stops, so it stays a note, not a
flag — it's the "cleared" illustration you run in the notebook, not a console incident.)*

## Question bank

**The core question — do this for everyone.**
- *Jump to 94, then 693. What's the difference?* Both are speed-zero; only one is a
  blockage. Proves why telemetry alone can't decide.

**The honesty test — do this one for skeptics.**
- *Why isn't the pit stop flagged?* The system says what it dismissed, and why. An
  invisible dismissal is indistinguishable from a broken app.
- *What happens if the camera can't see it?* (Segue to Bonus 3, graceful degradation.)

**The set-piece — corroboration.**
- *Run 693 with the solution, then flip to `--no-verify` and rerun.* Without the camera,
  Günther is only a double yellow. Corroboration is the escalator — that's the whole
  point of fusing two senses.

**The subtle one — for the sharp students.**
- *At 1692, why does the Safety Car hold after Nato drives off?* Because #23 is still
  stranded; the flag tracks the stranded car, and it's #23 *recovering* that would clear
  it — not #17.

## Why this is hard (talking points)

- **A stopped car is ambiguous.** Retirement vs pit stop vs spin-and-recover all start
  identically. One question — asked about the track's state at the *end* of a window —
  separates them. That's the design.
- **Never peek ahead.** The verifier waits for the 50-second tail to play before it
  looks. Confirm from what happened; don't ask about footage that hasn't happened.
- **Only spend a model call when it's earned.** A cheap detector gates the expensive
  model on a real *stop*, not a transient. Six Gemini calls per incident, not a stream.
- **The safety decision is code.** The model chooses words; a deterministic policy table
  chooses the flag — because a Safety Car must be explainable and repeatable.

## Troubleshooting

The instructor-grade table is in [`RUN_OF_SHOW.md`](RUN_OF_SHOW.md). The one you'll reach
for most: if anything is off for a participant, the first fix is **`source activate.sh`**.
