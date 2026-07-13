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
| 0:00–0:20 | Open on the finished thing. The problem. Kick off setup. |
| 0:20–0:45 | Notebook: explore telemetry, prove alignment, first Gemini call. |
| 0:45–1:25 | **Task 2 — the question.** The best discussion of the day. |
| 1:25–2:05 | **Task 3 — the port.** The board lights up. The moment. |
| 2:05–2:40 | Bonuses, self-paced. Circulate. |
| 2:40–3:00 | Wrap: the thesis, and where this goes next. |

## Morning-of: build the instructor stack

You demo the **solution**, which is fast (its sweep is concurrent). In a Cloud Shell tab:

```bash
source activate.sh
export VERIFIER_PACKAGE=solution.video_verifier    # run the reference, not the stubs
bash setup/all.sh                                   # ~15 min on a fresh project
python -m correlator.service                        # leave this running; it feeds the console
```

Open the console URL. Confirm you can jump to race-second **693** and see the full arc.
Keep a second browser profile for the "one sense" view if you like (`--no-verify`).

## Pre-flight (15 min before doors)

1. Instructor stack up; console reachable; correlator running the **solution**.
2. Jump to 693 once — confirm **Safety Car · corroborated** with **Cam05** and a blue
   Maserati in the narrative. This is your opening SHOW.
3. Jump to 94 (#33) — confirm **no flag**, "routine pit stop." This is your "honesty" beat.
4. Have the Colab link handy (from `bash setup/print_colab_link.sh`), and the student
   short link (`STUDENT_GUIDE.md` top) on a slide.
5. Know the one true fix: **`source activate.sh`**. It solves 80% of raised hands.

## The opening 20 minutes

| Clock | Segment | Immovable beat |
|---|---|---|
| 0:00–2:00 | Open on the finished thing | Günther: stop → confirmed → Safety Car |
| 2:00–8:00 | The problem: two senses | pit vs blockage look identical |
| 8:00–12:00 | Kick off setup | link prints FIRST → into the notebook |
| 12:00–20:00 | The plan | four tasks; Task 2 is the heart |

### 0:00–2:00 — Open on the finished thing

**SHOW:** the console, jumped to Günther (693). Let it play: telemetry flags the stop,
the Video Agent narrates *[ANALYZING] → CONFIRMED, track blocked, Cam05*, the flag
escalates to **Safety Car**, the Approve button appears.
**SAY:** *"A car has stopped in a blind corner. Two independent systems just agreed it's
real, drafted the report, and queued a Safety Car for a human to approve with one click.
The piece that made the camera talk — that's what you're building today."*
**WHY:** they should see the destination before they write a line. Everything later is
"how we got here."

### 2:00–8:00 — The problem

**SHOW:** jump to #33 (94) — a stationary car, **no flag**, "routine pit stop."
**SAY:** *"Same telemetry signature as Günther — speed zero. One's a retirement, one's a
pit stop. Telemetry cannot tell them apart. That's the whole reason we need a second
sense."*
**WHY:** plant the ambiguity. It justifies the camera, the persistence question, and the
"corroboration is the escalator" policy in one move.

### 8:00–12:00 — Kick off setup

**SHOW:** your own Cloud Shell running `bash setup/all.sh`; point at the **Colab link it
prints first**.
**SAY:** *"Click that link IN Cloud Shell — not from the guide. Cloud Shell lives inside
the console window, so it opens in the right project. Then start the notebook while the
stack builds. The notebook is real work, not filler."*
**WHY:** the wrong-session Colab trap is the #1 support ticket. Head it off out loud.

### 12:00–20:00 — The plan

**SAY:** *"Four tasks. Task 0 you just watched — feel the missing sense. Tasks 1 and 2
are in the notebook: get one Gemini call working, then write the question that does all
the work. Task 3 you port into the real component and the board lights up. Spend your
time on Task 2 — the question is the hack."*

## During the build — your moves

Checkpoint beats, matched to the student tasks. Circulate; don't lecture.

- **After Task 0 (~0:25):** ask the room *"why is a prolonged stop still only a double
  yellow?"* Right answer: no corroboration yet. This previews the whole policy.
- **Task 2 (~1:00) — run the discussion.** Put two prompts on the projector: *"is there a
  stopped car?"* vs *"is the line still blocked at the END?"* Let them argue. Land it:
  a car stopped at second 5 and gone by 50 is "a stopped car" in the first framing and
  correctly *cleared* in the second. **This is the best ten minutes of the day.**
- **Task 3 (~1:40) — the moment.** When the first student hits **Safety Car ·
  corroborated**, have them narrate it to the room. Then everyone's chasing it.
- **Watch for the slow sweep.** Someone will say "it takes a minute." Perfect: *"that's
  Bonus 1. Those six calls are independent — fan them out."*

## The wrap (last 20 minutes)

**SHOW:** the finished board again.
**SAY:** *"Deterministic code decided WHEN to spend a model call — a cheap detector saw a
stop. Only then did the expensive model get invited to look, at a bounded window, with
one well-posed question. That's why this runs on a handful of Gemini calls per race
instead of streaming video continuously — and it's why you can trust the answer enough
to put it in front of a race director."*
**WHY:** the thesis is the takeaway. If they remember one thing, make it this.

Optional close: the graceful-degradation question from Bonus 3 — *"we just made the
Safety Car depend on a camera. What if the camera can't see it? Should the system be
paralysed?"* It's the best design argument in the hack; leave them chewing on it.

## What healthy looks like (so you can spot sick)

- Setup finishes in ~15 min; the console answers; telemetry + console are green.
- Günther on the **solution** reaches Safety Car within ~10–15s of the window playing.
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
| Console shows a stale incident after a jump | correlator didn't reset on the jump | it clears state on a detected jump; give it a couple of fuse ticks, or restart |

## Event-morning checklist

- [ ] Instructor stack up; correlator running `VERIFIER_PACKAGE=solution.video_verifier`.
- [ ] Jump to 693 → Safety Car · corroborated, Cam05.
- [ ] Jump to 94 → no flag, "routine pit stop."
- [ ] Colab link + student short link on slides.
- [ ] `--no-verify` view ready for the "one sense" open.
- [ ] You can say the thesis from memory.
