# Formula E — The Proactive Race Control Observer (Challenge 3)

An automated, tireless set of eyes for Race Control. Two independent observers watch
the **2024 Berlin E-Prix (Round 10)** as it replays at 1 Hz — one reading the live
**telemetry**, one reading the trackside **CCTV** — and a deterministic **correlator**
fuses their reports, decides a recommended flag, drafts a preliminary incident report,
and queues it for a human official to approve with **one click**. The human decides;
the system prepares the decision.

This is a **hackathon**: the whole stack is given and running, and you build **one
focused component** — the **Video Verifier** — then watch it authorise a Safety Car in
a console you didn't have to write.

> **The house rule:** *deterministic code decides WHEN to spend a model call; the model
> decides WHAT it's looking at.* A cheap, reliable detector notices a stopped car; only
> then is Gemini invited to look — at a bounded window, with one well-posed question.

## Where to go (pick your reader)

| You are… | Read |
|---|---|
| **In the hackathon room, building** | [`STUDENT_GUIDE.md`](STUDENT_GUIDE.md) — the tasks, the commands, the "why" |
| **New to the codebase** | [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md) — the ten-minute orientation, read before editing |
| **Running the event** | [`RUN_OF_SHOW.md`](RUN_OF_SHOW.md) — morning-of, the opening, checkpoint beats |
| **Driving the demo** | [`DEMO.md`](DEMO.md) — the pit wall, the scripted moments, the question bank |
| **Finished early** | [`BONUS.md`](BONUS.md) — the stretch board (concurrency, livery ID, graceful degradation…) |
| **Lost in the tree** | [`FILE_INDEX.md`](FILE_INDEX.md) — every file, one line each |
| **Validating a fresh deploy** | [`SMOKE_TEST.md`](SMOKE_TEST.md) — a ~15-minute green-light pass |

## Quick start

```bash
source activate.sh        # venv + project + the starter/solution seam (VERIFIER_PACKAGE)
bash setup/all.sh         # prints your Colab link first, then stands up the stack (~15 min)
```

`setup/all.sh` deploys the data layer and the deployed agents (telemetry observer +
console) and stages the mosaics. The **correlator is NOT deployed** — it holds the file
you edit, so you run it locally and restart it in seconds:

```bash
python -m correlator.service --no-verify     # Task 0: telemetry only (the "one sense" state)
python -m correlator.service                 # Task 3: with YOUR verifier armed
```

## What you build

**The Video Verifier** — Gemini-powered CCTV confirmation of a telemetry stop. It reads
a **`gs://` mosaic *slice*** by time offset (no download, no ffmpeg, no frame extraction),
asks one persistence question, and fuses six per-camera-group replies into one verdict.
You implement three methods in `starter/video_verifier/verifier.py`
(`_prompt`, `_verify_group`, `_aggregate`); everything else runs for you. The complete
answer key is `solution/video_verifier/verifier.py`.

## Architecture (as built)

```
  simulator ──fe-telemetry──▶ Telemetry Observer ──┐
   (1 Hz replay)              (deterministic         │ fe-observations
                               stopped/prolonged/    ▼
                               recovered detector)   Correlator  ──fe-incidents──▶ Console
                                                     (fuse → flag → report)        (one-click
   mosaics bucket ◀── gs:// slice ── Video Verifier ─┘  ▲                            approve)
   (6× 2×2 CCTV mosaics)            (YOU build this;     │
                                     Gemini reads a      └── writes incidents/ + agent_status/
                                     bounded window)         to Firestore
```

Two senses, because one is ambiguous: telemetry can see a car stop and even tell a pit
stop apart by its GPS, but it can't judge whether a car stopped **out on the track** is a
real, lasting blockage. Telemetry raises its hand; the camera answers *is the racing line
actually blocked?* The flag policy is deterministic code
(`correlator/fusion.py`), not a model call — a safety decision must be explainable and
repeatable.

## Repo map

- `starter/video_verifier/` — **the file you build** (`solution/` is the answer key).
- `observers/telemetry/` — the deterministic detector (given; short, read it).
- `correlator/` — fusion (the flag policy), the runtime service, the report drafter.
- `shared/` — Pydantic contracts, the bus, the sim clock, the Gemini client, `verifier_pkg`.
- `frontend/` — the Race Control console (given).
- `simulator/`, `state_writer/` — the race replaying at 1 Hz (given).
- `setup/` — one-command provisioning ladder; `deploy/` — the underlying deploy scripts.
- `notebooks/fe_video_lab.ipynb` — your workbench (explore, prove alignment, tune the prompt).
- `prelab/` — one-time instructor tooling (mosaic building, etc.).

## Race context

The data is the real **Berlin R10** telemetry (`simulator/src/frames.jsonl.gz`, 1 Hz).
The demo incidents — the console's jump buttons — are real: **Günther #7** retires on
track; **Fenestraz #23 + Nato #17** stop together (Nato recovers, Fenestraz stays);
**Mortara #48** stops then recovers while #23 is still stranded; **Ticktum #33** makes a
routine pit stop (correctly *not* flagged).

## License

MIT.
