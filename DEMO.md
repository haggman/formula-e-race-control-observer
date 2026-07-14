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

> Telemetry can see a car stop — and it even tells a pit stop apart by GPS. What it can't
> do is judge whether a car stopped **out on the track** is a real, lasting blockage.

That's the reason the system has two senses. Telemetry raises its hand on an on-track stop;
the camera answers *is the racing line actually, still blocked?* And the flag itself is
decided by deterministic code, not the model — the model narrates, the policy decides.

## Running the demo

```bash
source activate.sh
export VERIFIER_PACKAGE=solution.video_verifier
python -m correlator.service         # leave running; it feeds the console
```

Drive the race from the console's bottom bar: the four **Jump to** buttons —
**#33 (pit — no flag)**, **Günther**, **Fenestraz + Nato**, **Mortara** — each jump to an
incident and auto-pause at the end of its window. **Pause/Resume**, **Clear**, and
**Restart** sit next to them. Every scripted moment below is a button.

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

## The scripted moments (click each button)

Each button jumps to the incident and auto-pauses at the end of its window. Watch the two
feeds converge into a recommendation.

1. **#33 (pit — no flag).** Telemetry sees a stationary car and *dismisses it out loud*:
   "routine pit stop, not a track incident." **The board stays green.** Open here to prove
   the system is as careful about false alarms as real ones — and that telemetry already
   handles the pit case without a camera.
2. **Günther — the hero.** Telemetry: *stopped* → *still stopped, confirmed*. Video:
   *[QUEUED]* until the window plays, then *[ANALYZING]* → **CONFIRMED, track blocked,
   Cam05** (a dark blue Maserati by the wall). Race Control escalates **double yellow →
   Safety Car · corroborated.** The Approve button appears. **This is the money moment.**
   *(A #48 yaw nearby settles on its own — note only, no flag: the detector doesn't spend a
   Gemini call on a twitch.)*
3. **Fenestraz + Nato.** #17 and #23 stop in the *same instant* and correctly fuse into
   **one** Safety Car incident, corroborated on **Cam07** — not two duplicate cards. Then
   **Nato recovers** and drives away: the rationale updates — *"#17 is racing again, but
   the flag stands while #23 remains stranded."* The board stops implicating a car that
   has already left.
4. **Mortara — the nuance beat.** #23 is still stranded; **#48 stops and then recovers at
   racing speed.** The **Safety Car HOLDS** — one car racing again does not clear a flag
   another car is still causing. A great "why didn't it stand down?" discussion.

*(Evans #9 has a big moment but never fully stops, so it stays a note, not a flag — that's
the "cleared" illustration you run in the notebook, not a console button.)*

## Question bank

**The core question — do this for everyone.**
- *Click **#33 (pit — no flag)**, then **Günther**. What's the difference?* Both are
  speed-zero — but telemetry dismisses #33 by its location, while #7 out on the track is
  the one the camera has to judge. That's why a second sense exists.

**The honesty test — do this one for skeptics.**
- *Why isn't the pit stop flagged?* The system says what it dismissed, and why. An
  invisible dismissal is indistinguishable from a broken app.
- *What happens if the camera can't see it?* (Segue to Bonus 3, graceful degradation.)

**The set-piece — corroboration.**
- *Click **Günther** with the solution running, then flip to `--no-verify` and click it
  again.* Without the camera, Günther is only a double yellow. Corroboration is the
  escalator — that's the whole point of fusing two senses.

**The subtle one — for the sharp students.**
- *On **Fenestraz + Nato**, why does the Safety Car hold after Nato drives off?* Because
  #23 is still stranded; the flag tracks the stranded car, and it's #23 *recovering* that
  would clear it — not #17.

## Why this is hard (talking points)

- **A stop out on the track is ambiguous.** A retirement and a spin-and-recover start
  identically (the pit case telemetry already handles by location). One question — asked
  about the track's state at the *end* of a window — separates them. That's the design.
- **Never peek ahead.** The verifier waits for the 50-second tail to play before it
  looks. Confirm from what happened; don't ask about footage that hasn't happened.
- **Only spend a model call when it's earned.** A cheap detector gates the expensive
  model on a real *stop*, not a transient. Six Gemini calls per incident, not a stream.
- **The safety decision is code.** The model chooses words; a deterministic policy table
  chooses the flag — because a Safety Car must be explainable and repeatable.

## Troubleshooting

The instructor-grade table is in [`RUN_OF_SHOW.md`](RUN_OF_SHOW.md). The one you'll reach
for most: if anything is off for a participant, the first fix is **`source activate.sh`**.
