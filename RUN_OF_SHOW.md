# Run of Show — Challenge 3: The Proactive Race Control Observer

For whoever delivers the event. Written assuming that person is **not** the author.

Every segment gives you three layers:
- **SAY** — lines that work. Use them or your own.
- **SHOW** — what's on the projector.
- **WHY** — what the beat is for, so you can improvise without breaking it.

The arc you're selling all day, in one sentence: *they build the eye that authorises the
Safety Car.* Everything serves the moment in Task 3 when the board flips from double
yellow to **Safety Car · corroborated**.

## The day at a glance

| When | What |
|---|---|
| 0:00–0:12 | **Solution demo → their starting point → the plan.** Kick off setup. |
| 0:12–0:35 | Notebook: explore telemetry, prove alignment, first Gemini call. |
| 0:35–1:15 | **Task 2 — the question.** The best discussion of the day. |
| 1:15–1:55 | **Task 3 — the port.** The board lights up. The moment. |
| 1:55–2:35 | Bonuses, self-paced. Circulate. |
| 2:35–3:00 | Wrap: the thesis, and where this goes next. |

## Morning-of: build the instructor stack

You demo the **solution**, which is fast (its sweep is concurrent). From a fresh project's
Cloud Shell:

```bash
git clone https://github.com/haggman/formula-e-race-control-observer.git
cd formula-e-race-control-observer
source activate.sh
export VERIFIER_PACKAGE=solution.video_verifier          # run the REFERENCE verifier,
                                                         # not the student stubs (which are
                                                         # unimplemented and would error)
bash setup/all.sh                                        # ~15 min on a fresh project
python -m correlator.service                             # leave this running; it feeds the console
```

Open the console URL. Confirm you can click the **Günther** button and see the full arc.

> **Why the correlator runs locally (not deployed):** it holds `verifier.py`, the exact
> file students edit — so running it locally means edit → Ctrl-C → rerun in seconds
> instead of a ~5-minute Cloud Build per change. It also lets you flip between the
> reference and the student build with one env var (`VERIFIER_PACKAGE`).

## Pre-flight (15 min before doors)

1. Instructor stack up; console reachable; correlator running the **solution**.
2. Click the **Günther** button once — confirm **Safety Car · corroborated** with **Cam05**
   and a blue Maserati in the narrative. This is your opening SHOW.
3. Click **#33 (pit — no flag)** — confirm the board stays **GREEN**, "routine pit stop."
   This is your "honesty" beat.
4. Have the Colab link handy (from `bash setup/print_colab_link.sh`), and the student
   short link (`STUDENT_GUIDE.md` top) on a slide.
5. Know the one true fix: **`source activate.sh`**. It solves 80% of raised hands.

## The opening ~12 minutes

Three beats: show them how it *should* work, show them where they *start*, then the plan.
The four "Jump to" buttons in the console — **#33 (pit — no flag)**, **Günther**,
**Fenestraz + Nato**, **Mortara** — each jump the sim to the incident and auto-pause at the
end of its window, so you just click and narrate.

| Clock | Segment | Immovable beat |
|---|---|---|
| 0:00–5:00 | How it should work (SOLUTION) | Günther escalates to Safety Car; pit stays green |
| 5:00–9:00 | Where you start (STARTER) | same stop, dead video column, only a double yellow |
| 9:00–12:00 | The plan + kick off setup | four tasks; Task 2 is the heart; link prints first |

### 0:00–5:00 — How it should work (demo the solution)

**SHOW:** click **Günther**. Let it play: telemetry flags the stop, the Video Agent narrates
*[ANALYZING] → CONFIRMED, track blocked, Cam05*, the recommendation escalates to **Safety
Car**, the Approve button appears. Then click **#33 (pit — no flag)**: a stationary car, and
the board **stays green** — "routine pit stop."
**SAY:** *"Two independent systems just agreed a car is really blocking the track, drafted
the report, and queued a Safety Car for one-click approval. And when a car simply pits —
same zero speed on telemetry — the system correctly does nothing. The piece that makes the
camera talk is what you build today."*
**WHY:** destination AND the core point — telemetry can *see* a stop, and even dismiss a
pit stop by location, but it can't tell whether a car stopped out on the track is a real,
lasting blockage — in one move. That's the old "the problem" segment folded in, which is
where the time savings come from.

### 5:00–9:00 — Where you start (demo the starting point)

**SHOW:** Ctrl-C the correlator and relaunch it with the verifier off, then click **Günther**
again:

```bash
python -m correlator.service --no-verify     # stands in for the verifier you haven't written yet
```

Now telemetry still nails the stop — *stopped, still stopped, confirmed* — but the **Video
Agent column is dead** and Race Control can only offer **double yellow — pending video
confirmation.**
**SAY:** *"This is where you begin. Telemetry works; the eye doesn't. The gap between what
you just saw and this — that's exactly the verifier you're going to write. Your code is what
turns this double yellow into a Safety Car."*
**WHY:** they see their start and the precise delta they'll close. Motivation = the gap.
*(Use `--no-verify` rather than the starter package here: the student stubs aren't written
yet, so they'd error — `--no-verify` is the clean, honest picture of "no verifier yet.")*

### 9:00–12:00 — The plan + kick off setup

**SHOW:** your own Cloud Shell running `bash setup/all.sh`; point at the **Colab link it
prints first**.
**SAY:** *"Four tasks. Task 1: get one Gemini call working in the notebook. Task 2 — the
heart — write the question that separates a retirement from a spin. Task 3: port it into
verifier.py and watch the board light up. Then bonuses. Spend your time on Task 2. Click the
Colab link IN Cloud Shell — not from the guide — and start the notebook while the stack
builds."*
**WHY:** they leave the intro knowing the path and where the effort goes. (The Cloud-Shell
click also heads off the #1 support ticket: a link clicked from the guide lands in the wrong
project.)

*(Restart the correlator with the verifier armed — drop `--no-verify` — whenever you want the
solution live again for reference later in the day.)*

## During the build — your moves

Checkpoint beats, matched to the student tasks. Circulate; don't lecture.

- **After the intro (~0:15):** ask the room *"why is a prolonged stop still only a double
  yellow?"* Right answer: no corroboration yet. This previews the whole policy.
- **Task 2 (~0:40) — run the discussion.** Put two prompts on the projector: *"is there a
  stopped car?"* vs *"is the line still blocked at the END?"* Let them argue. Land it: a car
  stopped at second 5 and gone by 50 is "a stopped car" in the first framing and correctly
  *cleared* in the second. **This is the best ten minutes of the day.**
- **Task 3 (~1:20) — the moment.** When the first student hits **Safety Car · corroborated**,
  have them narrate it to the room. Then everyone's chasing it.
- **Watch for the slow sweep.** Someone will say "it takes a minute." Perfect: *"that's
  Bonus 1. Those six calls are independent — fan them out."*

## The four incidents (for your live narration)

Click each button; it jumps and auto-pauses at the end of the window.

- **#33 (pit — no flag)** — a stationary car the system *dismisses out loud*: "routine pit
  stop." Board stays green. Proof it won't false-alarm.
- **Günther** — the hero. #7 retires on track → **Safety Car · corroborated, Cam05** (a dark
  blue Maserati by the wall). *(A #48 yaw nearby settles on its own — note only, no flag, no
  camera call: the detector doesn't spend a Gemini call on a twitch.)* #7 is retired, so its
  later tow-speed movement must **not** clear the flag.
- **Fenestraz + Nato** — #17 and #23 stop in the *same instant* and correctly fuse into
  **one** Safety Car incident, corroborated on **Cam07** — not two duplicate cards.
- **Mortara** — the nuance beat. #23 is still stranded; #48 then stops and later recovers at
  racing speed. **The Safety Car HOLDS** — one car racing again does not clear a flag another
  car is still causing. Great "why didn't it stand down?" discussion.

## The wrap (last 20 minutes)

**SHOW:** the Günther board again, escalated.
**SAY:** *"Deterministic code decided WHEN to spend a model call — a cheap detector saw a
stop. Only then did the expensive model get invited to look, at a bounded window, with one
well-posed question. That's why this runs on a handful of Gemini calls per race instead of
streaming video continuously — and it's why you can trust the answer enough to put it in
front of a race director."*
**WHY:** the thesis is the takeaway. If they remember one thing, make it this.

Optional close: the graceful-degradation question from Bonus 3 — *"we just made the Safety
Car depend on a camera. What if the camera can't see it? Should the system be paralysed?"*
It's the best design argument in the hack; leave them chewing on it.

## What healthy looks like (so you can spot sick)

- Setup finishes in ~15 min; the console answers; telemetry + console are green.
- **Günther** on the **solution** reaches Safety Car within ~10–15s of the window playing.
- A student's **starter** sweep takes ~60s (sequential) — that's expected, not broken.
- The Video Agent column is *never* silently blank while claiming a verdict — if it is,
  a publisher died (see below).

## Troubleshooting (instructor-grade)

| Symptom | Cause | Fix |
|---|---|---|
| Anything weird for a student | not activated | `source activate.sh` — the universal fix |
| `FAILED_PRECONDITION: service agents being provisioned` | fresh-project Vertex agent still propagating | wait a few minutes; setup provisions it early and the verifier retries. Not a code bug. |
| Mosaic reads 403 | Vertex service agent lacks bucket read | `bash setup/2_provision_vertex_agent.sh` |
| Colab opened the wrong project | link clicked from the guide, not Cloud Shell | `bash setup/print_colab_link.sh`, click in Cloud Shell |
| Video column blank but logs say "CONFIRMED" | a publisher failed on startup (silent failure) | restart the correlator; check the fe-observations publisher line in its logs |
| Flag won't escalate | verdict not `blocked`, or correlator not restarted after an edit | standalone CLI first; then Ctrl-C + rerun |
| Console shows a stale incident after clicking a button | correlator didn't reset on the jump | it clears state on a detected jump; give it a couple of fuse ticks, or restart |

## Event-morning checklist

- [ ] Instructor stack up; correlator running `VERIFIER_PACKAGE=solution.video_verifier`.
- [ ] Click **Günther** → Safety Car · corroborated, Cam05.
- [ ] Click **#33 (pit — no flag)** → board stays green.
- [ ] Colab link + student short link on slides.
- [ ] `--no-verify` relaunch rehearsed for the "starting point" beat.
- [ ] You can say the thesis from memory.
