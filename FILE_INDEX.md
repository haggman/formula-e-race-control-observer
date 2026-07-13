# File Index — Challenge 3

A map of every file and folder in this repo, one line each. Lost? Start here.

## Top-level docs

| Path | What it is |
|---|---|
| `README.md` | The front door + the "pick your reader" router. |
| `HOW_IT_WORKS.md` | The ten-minute orientation. **Read before editing code.** |
| `STUDENT_GUIDE.md` | The build manual — four tasks, commands, the "why". |
| `RUN_OF_SHOW.md` | The instructor's minute-by-minute (SAY / SHOW / WHY). |
| `DEMO.md` | Demo material — the pit wall, scripted moments, question bank. |
| `BONUS.md` | The stretch board (concurrency, livery ID, graceful degradation…). |
| `SMOKE_TEST.md` | ~15-minute green-light pass for a fresh deploy. |
| `FILE_INDEX.md` | This file. |
| `docs/HACK_DESIGN.md` | The settled design brief (how this hack was built). |
| `docs/DEPLOYMENT.md` | Deeper deployment notes. |
| `PLAN.md` | Historical design + progress log (background). |

## The Video Verifier (what students build)

| Path | What it is |
|---|---|
| `starter/video_verifier/verifier.py` | **The file you build.** Three stubs: `_prompt`, `_verify_group`, `_aggregate`. `_sweep` is given but sequential (Bonus 1). |
| `starter/video_verifier/__init__.py` | Package marker for the student build. |
| `solution/video_verifier/verifier.py` | The complete reference answer key (the demo runs this). |
| `solution/video_verifier/__init__.py` | Package marker for the reference. |
| `starter/__init__.py`, `solution/__init__.py` | Parallel top-level packages holding ONLY the verifier. |
| `shared/verifier_pkg.py` | The starter/solution seam — resolves `VERIFIER_PACKAGE` to the chosen verifier. |

## Given components

| Path | What it is |
|---|---|
| `observers/telemetry/detector.py` | The deterministic "when": stopped / prolonged / recovered / yaw / pit-lane guard. Short — read it. |
| `observers/telemetry/consumer.py` | Rolling window, latches, heartbeats; drives the detector. |
| `correlator/fusion.py` | The flag policy (deterministic). Corroboration is the escalator. |
| `correlator/service.py` | The runtime: buffer, fuse, the "wait for the window" gate, announce. Loads the verifier via `verifier_pkg`. |
| `correlator/reporter.py` | Gemini drafts the human-readable incident narrative. |
| `correlator/prompts.py` | The reporter's prompt. |
| `frontend/` | The Race Control console (Cloud Run service + static UI). |
| `simulator/` | The race, replaying at 1 Hz; `src/frames.jsonl.gz` is the real Berlin R10 telemetry. |
| `state_writer/` | Writes the latest frame as "now" to Firestore. |

## Shared library

| Path | What it is |
|---|---|
| `shared/models.py` | Pydantic contracts (samples, observations, incident, flag, report). |
| `shared/observation_bus.py` | Pub/Sub publishers + subscriber for the observation/incident buses. |
| `shared/clock.py` | The sim clock (reads the simulator's race-time). |
| `shared/gemini.py` | The Vertex Gemini client + async retry helper. |
| `shared/heartbeat.py` | Firestore agent-status heartbeats. |
| `shared/lifecycle.py` | Graceful stop / idle watchdog / deadman for the services. |

## Setup & deploy

| Path | What it is |
|---|---|
| `activate.sh` | `source` it first — venv, project, and the `VERIFIER_PACKAGE` seam. |
| `setup/all.sh` | ONE command: prints the Colab link, then runs steps 1–8, then verifies. |
| `setup/print_colab_link.sh` | Prints the one-click notebook link (run it *in Cloud Shell*). |
| `setup/1_enable_apis.sh` | Step 1 — enable APIs. |
| `setup/2_provision_vertex_agent.sh` | Step 2 — **provision the Vertex AI service agent early** (defuses `FAILED_PRECONDITION`). |
| `setup/3_setup_firestore.sh` | Step 3 — Firestore. |
| `setup/4_stage_mosaics.sh` | Step 4 — stage the CCTV mosaics into your bucket. |
| `setup/5_deploy_state_writer.sh` | Step 5 — State Writer (worker pool). |
| `setup/6_deploy_simulator.sh` | Step 6 — the simulator. |
| `setup/7_deploy_telemetry_observer.sh` | Step 7 — Telemetry Observer (worker pool). |
| `setup/8_deploy_console.sh` | Step 8 — the Race Control console. **Correlator is NOT deployed** (runs locally). |
| `setup/verify.sh`, `setup/verify_checks.py` | Data-layer green-light. |
| `setup/_lib.sh` | Shared helpers (`require_activation`, `banner`). |
| `deploy/*.sh` | The underlying worker scripts each setup step wraps. |
| `deploy/provision_vertex_agent.sh` | The extracted Vertex service-agent provisioning (the landmine fix). |
| `deploy/deploy_app.sh` | Deploy the whole app tier (telemetry + correlator + console) — for the ship-it capstone. |
| `deploy/deploy_correlator.sh` | Deploy the correlator to Cloud Run (Bonus 7). |
| `deploy/verify_app.sh`, `deploy/verify_app_checks.py` | App-tier green-light (correlator-tolerant — it runs locally). |

## Notebook, prelab, scripts

| Path | What it is |
|---|---|
| `notebooks/fe_video_lab.ipynb` | Your workbench: explore telemetry, prove alignment, first Gemini call, tune the prompt. |
| `prelab/` | One-time instructor tooling (mosaic building, camera ordering). Not student-facing. |
| `scripts/` | Local probes/catalogue harnesses (run them, don't edit them). |

## Retired (removed from this build)

`observers/video/verifier.py` moved into `starter/`+`solution/`; the old streaming-observer
chain and `notebooks/verify_camera_mapping.ipynb` are gone (recoverable from git history).
