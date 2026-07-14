# How it works — the ten-minute orientation

Read this before you write any code. It won't teach you the API; it will teach you
why the system is shaped the way it is — and that's what makes the difference
between a verifier that works and one that confidently lies to Race Control.

---

## The system in one paragraph

A Formula E race replays at 1 Hz. Two independent observers watch it. The
**Telemetry Observer** reads the cars' own data and spots a car that has stopped —
cheap, deterministic, instant. The **Video Verifier** (the piece you build) reads
the trackside CCTV and answers one question about what the cameras actually show.
A **Correlator** fuses the two into a single recommendation — *double yellow*,
*safety car*, or *nothing* — and puts it in front of a human official who approves
it with one click. **The human decides. The system prepares the decision.**

![Architecture — two senses, one flag](docs/architecture.svg)

---

## Why one sense isn't enough

Here is the entire problem, in one fact:

> **Telemetry can see that a car has stopped. It cannot see WHY, or whether it lasts.**

Telemetry isn't naive about the pit lane — it knows the car's GPS, so a car sitting in its
pit box is flagged as a routine pit stop and dismissed. The hard case is a car that stops
*out on the track*. Speed is zero, but is it a retirement that's blocking the racing line,
or a spin the driver will gather up and drive out of? Is it in a dangerous spot, or safely
in a run-off? Is it *still* there a minute later? Telemetry can't answer any of that with
confidence.

So telemetry raises its hand — *"car #7 has been stationary for 18 seconds"* — and the
cameras answer the question telemetry can't: **is the racing line actually, still
blocked?**

That's the whole architecture. Two senses, because a stopped car on track is ambiguous
until someone looks.

---

## The clock: one number holds it all together

Everything keys off **race-time in seconds** — seconds since the green flag.

- The simulator publishes a frame per race-second and exposes `race_time_s`.
- Telemetry observations are stamped in race-time.
- **The camera mosaics are 1 FPS, starting at race-second 0.** So *mp4 offset N
  equals race-second N.* That single fact is what lets you ask Gemini for "the
  footage around the stop" without any clock conversion at all.

Don't take that on faith — the notebook makes you prove it, by reading the clock
burned into a frame. Everything you build rests on it.

---

## Why 24 cameras became 6 Gemini calls

The circuit has 24 CCTV cameras. Asking Gemini about each one, per incident, is 24
calls — slow and expensive.

Instead the cameras are pre-tiled into **six 2×2 mosaics**, each holding four
physically-adjacent cameras in a fixed layout: top-left, top-right, bottom-left,
bottom-right. One call covers four cameras. **24 cameras → 6 calls.**

The model tells you which *panel* it saw the incident in (`"TL"`, `"BR"`…), and the
code maps that panel back to a real camera ID. That's why the verdict can say
"Cam05" when the model never knew Cam05 existed.

---

## What makes the Video Verifier look — and when

Two rules, and both matter more than they look.

**Rule 1: only a STOP earns a look.** Not a yaw spike, not hard braking. A car can
twitch and drive on; that resolves itself in telemetry and costs nothing. Only a
stopped car is a candidate blockage — and only a blockage is something a camera can
actually confirm.

**Rule 2: wait for the footage to exist.** The verifier looks at a 60-second window:
10 seconds *before* the stop and 50 seconds *after*. In a replaying race, the
"after" hasn't happened yet at the moment the car stops. So the correlator refuses
to call Gemini until the race clock has passed the end of that window.

That delay is not a limitation — **it is the design.** It's what lets one question
do all the work.

---

## The question that does all the work

A naive prompt asks: *"Is there a stopped car?"*

That question cannot tell a retirement from a spin. A car can be stopped at second 5
and gone by second 50. Both are "a stopped car." One needs a Safety Car; the other
needs nothing.

The question the system actually asks is:

> **"By the END of this window, is the racing line still BLOCKED, or did it CLEAR?"**

That's a question about the **track's state at the end of the window**, not about a
car's identity at a moment. Ask it that way and a single answer separates a genuine
retirement from a driver who spun, gathered it up, and drove away.

Writing that prompt is the heart of what you're building. Spend your time there.

---

## The journey of one incident (Günther, race-second 693)

1. **693** — Telemetry: `car 7 stopped on track for 6s`. Correlator opens an
   incident. Not corroborated → **double yellow, pending video confirmation.**
2. **711** — Telemetry: `STILL stopped after 18s — confirmed blockage`.
3. **~695** — The Video Agent says **`[QUEUED]`**: it knows a review is needed, and
   says it's waiting for the 13:15:23–13:16:23 window to play. *It does not guess.*
4. **748** — The window has played. **`[ANALYZING]`** — six camera groups, in
   parallel, one question each.
5. **~758** — Gemini reports a dark blue Maserati stopped by the wall in the
   top-left panel of one mosaic, still there at the end of the clip. Panel → Cam05.
   Verdict: **blocked**.
6. Correlator fuses: telemetry stop **+** video blocked = corroborated. The
   recommendation escalates to **SAFETY CAR**, and the Approve button appears.

A human clicks. Total Gemini spend: **six calls.**

---

## How two senses become one flag

The flag policy is **deterministic code, not a model call** — a safety decision must
be explainable and repeatable. The model narrates; the policy decides.

| Situation | Recommendation |
|---|---|
| Stop, **video confirms blocked** | **SAFETY CAR** (corroborated) |
| Stop, no video corroboration yet | DOUBLE YELLOW — *pending confirmation* |
| Video says the line **cleared** | NONE — the false-alarm veto |
| Telemetry says the car is **racing again** | NONE — it recovered |
| Car stationary **in the pit lane** | Note only. No flag. |

Note the last two rows: the system is as proud of what it *doesn't* flag as what it
does. A recommendation engine that cries wolf gets ignored, and an ignored safety
system is worse than none.

---

## The file map

```
observers/telemetry/     the deterministic detector  (GIVEN — read it, it's short)
  detector.py              stopped / prolonged / recovered / yaw / pit-lane guard
  consumer.py              rolling window, latches, heartbeats

starter/video_verifier/  ← YOU BUILD THIS
  verifier.py              the prompt, the Gemini call, the verdict

correlator/              the supervising agent  (GIVEN)
  fusion.py                correlate observations → incidents → a flag
  service.py               buffer, tick, the "wait for the window" gate, announce
  reporter.py              Gemini drafts the human-readable narrative

shared/                  contracts + plumbing (models, bus, clock, gemini client)
frontend/                the Race Control console  (GIVEN)
simulator/               the race, replaying at 1 Hz  (GIVEN)
```

**Read `correlator/service.py::_stop_time` and `_maybe_verify`.** They are the two
functions that decide when *your* code gets called. Nothing else will teach you the
contract faster.

---

## Nine facts that will save you a debugging hour

Every one of these is a bug we actually shipped and then had to hunt down.

1. **Don't ask the model to verify a twitch.** We once ran the verifier on a yaw
   spike. Gemini reported a car "crashed and stranded against the barrier" — while
   telemetry showed that same car doing 134 km/h. It wasn't lying; we asked it to
   confirm something that had already resolved, and it obliged. **Only ask a
   question the footage can actually answer.**

2. **Never peek ahead.** If you verify *at* the stop instead of after the window has
   played, you're asking about footage that hasn't happened. The 50-second tail is
   the entire reason one question can separate retirement from recovery.

3. **Tow speed is not racing speed.** A retired car under tow hit 115 km/h, tripped
   our "recovered!" threshold, and cleared a Safety Car that should have held. A
   racing lap is 150-220 km/h. Thresholds encode assumptions — validate them against
   the real data, which is right there in the repo.

4. **A retired car never recovers.** The data carries an `is_retired` flag. Trust it
   over a speed reading.

5. **An incident's identity can't include its car list.** Cars *accrete* — a second
   car stops 50 seconds into the same crash. We keyed incidents on the car list, so
   the key changed when a car joined, and the "same" incident got announced twice and
   **verified twice** (two billable Gemini sweeps for one crash). Key on something
   that doesn't change as the incident grows.

6. **Nearby in time ≠ the same incident.** A yaw on car #48 got swallowed into car
   #7's stop 49 seconds later, and #48 was implicated in a Safety Car it had nothing
   to do with. Correlate on identity and location, not just the clock.

7. **Say what you dismissed.** When the pit-lane guard correctly ignores a stopped
   car, the board shows *"routine pit stop, not a track incident."* An invisible
   dismissal is indistinguishable from a broken system — and you will not know which
   one you're looking at.

8. **Silent failure is the real enemy.** A publisher once died quietly on startup.
   The correlator went on logging `video verdict → BLOCKED` and `CONFIRMED INCIDENT`
   while publishing *nothing*, and two UI columns sat empty. The logs said everything
   was fine. **Make failure loud.**

9. **Gemini reads `gs://` as itself, not as you.** When you pass a `gs://` URI, the
   *Vertex AI service agent* fetches the file — a different identity from your
   service account. On a fresh project it must be provisioned and granted read on the
   bucket, and that takes minutes to propagate. If you get
   `400 FAILED_PRECONDITION: service agents are being provisioned`, that's this, and
   the fix is to wait, not to change your code.

---

## The thesis

If you take one idea away from this hack, take this:

> **Deterministic code decides WHEN to spend a model call. The model decides WHAT
> it's looking at.**

A cheap, dumb, reliable detector notices something. Only then does the expensive,
brilliant, occasionally-hallucinating model get invited to look — at a bounded
window, with one well-posed question. That's why this system runs on a handful of
Gemini calls per race instead of streaming video continuously, and it's why you can
trust its answer enough to put it in front of a race director.
