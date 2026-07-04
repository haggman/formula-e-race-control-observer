# Hack 3 — Proactive Race Control Observer (Safety)

> **Status:** Design phase. This is the living plan + progress doc for the third
> Formula E hackathon. Concept validated against real data; not yet built.
> Last updated: 2026-07-03.

---

## The challenge (from the 7-challenge plan doc — Challenge 3: Safety)

Safety decisions happen in milliseconds. Build an agent that acts as a tireless,
automated set of eyes for Race Control. It continuously ingests live CCTV and
telemetry. On detecting an incident (collision, stopped car, debris), it doesn't
just alert — it calculates severity, flags the precise timecode and location,
drafts a preliminary incident report, and queues a recommended yellow-flag /
safety-car deployment for a human official to approve with one click.

Named Google toolset in the source doc: Gemini (multimodal), Vertex AI Vision,
BigQuery, Cloud Functions, Pub/Sub. See "Tooling decisions" for what we actually
adopted and why.

---

## Design decision — a 3-agent sensor-fusion architecture

The signature teaching move across Hacks 1 & 2 is **deterministic code decides
*when*, the model decides *what*.** We keep that spine and extend it into a
multi-agent, multi-modal fusion pattern:

- **Agent 1 — Video Observer.** Watches a CCTV feed via the **Gemini Live API
  (BiDi streaming) at ~1 FPS**. Looks for *persistent conditions* — a stopped or
  slowed car, debris on the racing line, dust/smoke. Reports what it sees.
- **Agent 2 — Telemetry Observer.** *Not* streamed. A **deterministic grading /
  scoring pass** over the 20 Hz telemetry stream (speed → 0, yaw-rate spike, hard
  longitudinal deceleration) decides when something happened and **invokes** the
  agent to characterize it. This mirrors the `scorer.py` pattern from the other
  two hacks.
- **Agent 3 — Reporting / Correlator (the supervisor).** Receives reports from
  Agents 1 & 2 (**via A2A**), correlates them within a tolerance window, dedupes,
  scores severity, drafts the incident report, and queues the flag recommendation
  for one-click human approval.

Agents 1 & 2 are naturally separate Cloud Run services, which is exactly why
**A2A** is the right transport (vs. in-process ADK sub-agents). ADK is 1.0 GA and
`RemoteA2aAgent` makes wiring a few lines.

### Why the video agent is framed as "persistent conditions," not "impact frames"

The Gemini Live API caps streamed video at **1 frame/second** and is explicitly
"unsuitable for fast-changing video such as play-by-play in high-speed sports." A
crash on a 140+ km/h car happens between frames. But the *aftermath* — a stopped
car, debris, a dust plume — persists for seconds and reads clearly at 1 FPS. That
is what Race Control actually needs to catch, and it's what we validated in the
data (see Findings).

---

## Given vs. student build (keep the house pattern)

Consistent with Hacks 1 & 2: the full stack is given and running; the student
builds **one focused component**.

**GIVEN (instructor stack):**
- The two stream sources (CCTV clip feed + telemetry replay), following the
  simulator → Pub/Sub → Firestore pattern already used in the other hacks.
- The Race Control console UI (the one-click approve/reject surface) — plumbing,
  like the pit wall and companion screen were.
- The multi-agent skeleton + A2A wiring.
- The reference/solution build (answer key + "stuck?" escape hatch).

**STUDENT BUILDS (the lesson):** the **Reporting / Correlator agent (Agent 3)** —
fuse the two observers' reports, dedupe, score severity, draft the report, queue
the recommendation. That's where the judgment lives.

- Alternative / secondary build target or a later tier: the **Video Observer's
  prompt/persona** (what counts as an incident, how to describe it).
- Possible advanced tier: swap the telemetry trigger for a Vertex AI Vision
  stream as the "always-watching" detector (see Open Questions).

---

## Tooling decisions

- **Gemini multimodal + Live API (1 FPS)** for the video observer — adopted.
- **Deterministic telemetry grading** for the telemetry trigger — adopted (over
  streaming the numeric data through BiDi, which the Live API isn't built for).
- **A2A / ADK** for inter-agent reporting — adopted.
- **Vertex AI Vision** — *not* core. It's the named tool in the source doc and
  could serve as a given "always-watching" detection layer or an advanced tier,
  but a genuine crash detector needs a custom AutoML model + labeled data we
  don't have coverage for. Parked as optional. (Gemini Live now covers the
  realtime-multimodal need the doc reached for Vision to fill.)

---

## Data findings (validated 2026-07-03)

Source: Berlin 2024 R10, staged at `gs://class-demo/formula-e/`. Pulled the 8
curated `cross_challenge/crash_training/` clips + R10 structured data locally to
`_video_scratch/` (root of fe-hacks, not in this repo).

**1 FPS aftermath legibility — CONFIRMED.** Across all 8 clips, the aftermath
(stopped/spun car, dust plume, debris on track, car against wall) reads clearly
in 1-second stills, even when the impact instant falls between frames.

**The two-observer sync is real and tight (~seconds).** Two R10 incidents
triangulate across telemetry + race control + video:

| Incident | Telemetry | Race control | Video clip | Notes |
|---|---|---|---|---|
| **Fenestraz #23 — ~13:32:11 UTC** | speed → 0, sustained ~1000s (retirement) | VAN/FEN collision investigation 13:31:06; **SC deployed 13:32:28** | `BER_R10_GOOGLE_CRASH_01` (onboard #23 into wall) | **Hero pick** — single identifiable car, cleanest signal |
| Günther #7 — ~13:15:32 UTC | stops at Turn 1–2 | yellow T1/T2 13:15:46; **SC 13:16:12** | `BER_R10_GOOGLE_CRASH_02` (wide/CCTV-like) | better overhead angle |

**Clock caveat (design around it):** the three clocks agree within seconds, not
exactly — e.g. telemetry stop 13:32:11 vs SC message 13:32:28 (~17s reporting
lag). The correlator must fuse observations within a **tolerance window**, not by
exact-timestamp match. This is a realistic teaching detail, not a bug.

**Real CCTV is available for the hero moment:** 13:32 UTC = 15:32 Berlin local,
inside the `footage/berlin_r10/cctv/` `15:12–15:42` time block. We can extract the
actual overhead camera clip later (a few files, not the 153 GB set).

### The 8 crash-training clips (reference)

Berlin R10 (×2, our round), Berlin R09 di Grassi donut, Rd14 Mortara, Portland
R13 (×2 incl. an unnamed UUID montage), Tokyo R05, Monaco R08. Mix of broadcast
wide (CCTV-like) and onboard angles. Contact sheets in `_video_scratch/_sheets/`.

---

## Open questions / decisions pending

1. **Student build target:** confirm Agent 3 (correlator) as the single build,
   with Video Observer persona as a stretch tier. (Leaning yes.)
2. **BiDi placement:** video observer only (telemetry stays a loop)? Or run both
   as BiDi for symmetry / teaching value?
3. **Vertex AI Vision:** leave parked, or include as an advanced "always-watching"
   tier?
4. **Hero incident:** lock Fenestraz #23 (13:32) as the demo anchor? Günther #7 as
   the secondary.
5. **Stream fidelity:** pre-clipped incident MP4s (reliable) vs. simulated live
   CCTV feed. Leaning pre-clipped + telemetry-trigger "always watching" illusion.
6. **Tier structure & timings** for the Student Guide (model on Hacks 1 & 2's
   tiered format).

---

## Progress log

- **2026-07-03** — Reviewed Hacks 1 & 2 patterns and the 7-challenge source doc.
  Confirmed Hack 3 = Safety / Proactive Race Control Observer. Researched Vertex
  AI Vision (current), Gemini Live API (1 FPS video cap), and A2A/ADK (1.0 GA).
  Settled the 3-agent sensor-fusion architecture and the given-vs-build split.
  Pulled the 8 crash clips + R10 structured data; validated 1 FPS aftermath
  legibility, the telemetry↔race-control↔video sync (Fenestraz #23 hero
  incident), and the ~seconds clock offset. Created this repo folder + plan doc.

## Next steps

1. Resolve the open questions above (esp. build target, hero incident, Vision).
2. Draft the architecture diagram (given = grey, build = amber), matching the
   other two hacks' `docs/architecture.svg` convention.
3. Extract the real R10 CCTV clip(s) for the hero window.
4. Scaffold the repo (starter/ + solution/ + setup/ + docs) following the
   established layout.
5. Write STUDENT_GUIDE tiers, RUN_OF_SHOW, HOW_IT_WORKS, DEMO.
