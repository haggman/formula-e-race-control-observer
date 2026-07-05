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
inside the `footage/berlin_r10/cctv/` `15:12–15:42` time block.

**CCTV coverage finding (2026-07-03):** the fixed cameras are sparse and the
Fenestraz #23 crash falls in a **blind spot** at the T15 braking zone. Verified
three adjacent cameras at 15:32 local — Cam21 (labelled PIT ENTRY), Cam20 (T14),
Cam19 (T13) — none frame the incident (empty track, correct timestamps). Camera
overlays give ground-truth positions; pit-in is "approaching T15" per the data
dictionary. Implication for the demo: don't rely on a perfect fixed-CCTV angle of
this exact incident. Options: (a) use the curated `BER_R10_GOOGLE_CRASH_02` clip
(elevated R10 incident view, already local) as the CCTV source; (b) re-anchor the
video side to a better-covered incident (Günther #7 / T1–2 at 13:15, west-end
cameras Cam2/Cam7). The telemetry/correlator work is unaffected by this choice.

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

- **2026-07-03 (build)** — Scaffolded the repo (skeleton, pyproject, requirements,
  .gitignore, README). Wrote `shared/models.py` (TelemetrySample, Observation,
  CorrelatedIncident, IncidentReport). Built the deterministic telemetry detector
  (`observers/telemetry/detector.py`) — primary STOPPED_CAR signal + secondary
  HARD_DECEL / YAW_SPIKE hints — and validated it against real R10 telemetry with
  `scripts/probe_telemetry.py`. Results: STOPPED_CAR flags cars [2, 7, 17, 23, 33,
  48], which map cleanly onto the documented race-control incident timeline (the
  13:32 collision fires on **both** #23 Fenestraz and #17 Nato; #7 Günther at
  13:15). Recalibrated HARD_DECEL against the measured braking envelope (normal
  1.5s drops peak ~68 km/h and always exit at speed; gate on fast-entry + big-drop
  + slow-exit) — cut a clean car's false positives from 56 to ~0. The "when"
  trigger is done and data-validated.

- **2026-07-03 (video observer + external verification)** — Built the Video
  Observer: `observers/video/prompts.py` (Race Control persona + strict JSON
  contract), `frame_source.py` (ffmpeg 1 FPS extraction with absolute-UTC
  mapping), `observer.py` (Gemini Live streaming loop + `--dry-run`). Validated
  locally: 1 FPS extraction, UTC anchoring (frame 0 → 13:32:00, on the telemetry
  stop), prompt assembly, and the JSON→Observation parser (positive/negative/
  garbage). Live inference itself runs in Patrick's Qwiklabs Cloud Shell (needs
  Vertex/Gemini creds). **External verification:** public race reports
  independently confirm the Nato(#17)/Fenestraz(#23) collision into the wall (our
  hero incident), Günther(#7) DNF stopped trackside, the safety car adding 3 laps
  (41 total), Vandoorne(#2) front-wing pit, and da Costa(#13) winning. Four
  independent sources now agree (telemetry, race-control log, video, press).

### Decision — web/search is a grading oracle, NOT a runtime tool
Use web research to build a **ground-truth incident timeline** for scoring the
agents (did they catch what really happened?), like the other hacks' probe/smoke
tests. Keep Google Search OUT of the live observer/correlator path to preserve
**time-honesty** — a live Race Control system must not be able to look up the
race result and spoil its own outcome.

- **2026-07-03 (correlator)** — Built the supervising Correlator:
  `correlator/fusion.py` (pure: groups Observations into CorrelatedIncidents in a
  20s tolerance window, merges cars/location, marks corroboration, scores
  severity, and a deterministic flag policy), `prompts.py` + `reporter.py` (drafts
  the IncidentReport — deterministic template offline, Gemini narrative in Cloud
  Shell). Validated with `scripts/probe_correlator.py`: real telemetry stops +
  a stubbed video Observation fuse so that the **#23/#17 hero incident is the only
  corroborated one → Safety Car at T15 (sev 100)**; all lone stops → double-yellow.
  **Design win:** corroboration is the escalator to Safety Car. A single telemetry
  stop is ambiguous (Vandoorne #2's "stop" is really his front-wing PIT stop, a
  classic false positive); it takes the video observer confirming the car is on
  the racing surface to justify a full SC. This is the whole thesis of the hack,
  visible in the probe output. **Refinement noted:** a pit-lane GPS filter would
  further suppress pit-stop false positives — a natural student-build candidate.

- **2026-07-04 (data layer, borrowed from Ch2)** — Built the telemetry data plane.
  Added the `RaceFrame`/`FrameCar` 1 Hz contract to `shared/models.py` (+ a
  `to_samples()` bridge to the detector). `notebooks/build_frames.py` downsamples
  R10 20 Hz telemetry to **2880 one-Hz frames** (`simulator/src/frames.jsonl.gz`,
  1.5 MB, bundled in the image), each stamped with the real UTC — validated: the
  incident second (race_time_s=1691 → 13:32:11) shows #23 stopped+retired at the
  exact GPS, and the detector still fires STOPPED_CAR on the 1 Hz stream (13:32:12,
  within tolerance). `simulator/` is the Ch2 simulator (ReplayClock verbatim +
  publisher/frame_loader/config/main + Dockerfile + deploy.sh) → publishes to
  Pub/Sub `fe-telemetry`. `state_writer/` is the **Cloud Run Worker Pool** (Pub/Sub
  PULL → Firestore `race_states/{race_id}`) borrowed from the fan-concierge worker,
  with `deploy/deploy_state_writer.sh` (manual `--instances` scaling). Whole plane
  round-trips offline; only Pub/Sub publish + Firestore write need GCP (Cloud Shell).

### Video ingestion — prebuilt 2×2 track-ordered mosaics (settled 2026-07-04)
Rather than 24 always-on Live sessions, the video observer watches **2×2 camera
mosaics** (multiviewer style). Decisions:
  - **Quad (2×2), not hex** — keeps ~50% more per-panel detail; a stopped car is
    legible even at 512 px total (verified on real CCTV). Debris/fine text are the
    casualties, but telemetry supplies car numbers so that loss is free.
  - **Grouped in TRACK ORDER** — the 4 cameras in a mosaic are physically
    consecutive, so a car's progression stays visible panel→panel and
    boundary-spanning incidents show in one grid. Panels arranged in travel order
    (TL→TR→BL→BR). Order derived from each camera's burned-in turn label
    (CAM19=T13, CAM20=T14, CAM21=pit-entry…), NOT by assuming Cam#=track order.
    24 cams → 6 groups → ≤6 Live sessions.
  - **PREBUILT + prestaged**, the video twin of `frames.jsonl.gz`. A build job
    downsamples to 1 FPS, tiles 2×2 with panel labels, and encodes a tiny mp4 per
    group (**measured: 30 s = 593 KB, ~187× smaller than source; ~58 MB per group
    for the full race; ~350 MB for all 24 cams vs ~150 GB raw**). Students copy
    their group's mosaic into their own project and stream locally. Runtime feeder
    just replays the prebuilt mosaic paced by the sim clock — no live compositing.
  - A `manifest.json` records each mosaic's group→cameras, panel layout, and
    `start_utc` (the anchor that keeps video observations aligned to telemetry).
  - Compositing makes all-cameras-hot cheap, so telemetry→camera CUEING drops to
    an optional optimization (keeps both observers fully independent).

### Sync design (settled)
Both feeds are paced by ONE `ReplayClock` and every output carries the real
race-UTC; the correlator joins on time within its ±20 s window — the streams are
never frame-locked to each other. The video feeder polls the simulator's
`/status` `race_time_s` to share that one clock. The Live API's 1 FPS cap means
the video-in-the-loop demo runs at 1× (house guidance already says "demo at 1×").

- **2026-07-04 (mosaic pipeline)** — Built `notebooks/build_camera_mosaics.py` +
  `camera_groups.example.json`: from a track-ordered group config it downsamples 4
  aligned camera sources to 1 FPS, tiles 2×2 in travel order with panel labels,
  encodes a tiny 1 FPS mp4 per group, and writes `manifest.json` (group→cameras,
  layout, start_utc). Per-panel `src_offset_s` aligns the CCTV blocks (they start
  at different local times). Proven locally on the 3 real R10 cams (+1 pad): a
  40 s 2×2 mosaic = 0.73 MB, panel labels render, and all three real panels show
  the SAME clock (15:32:13) — alignment confirmed. Full generation (6 groups, all
  24 cams) runs in Cloud Shell against the gs:// sources in the example config.

- **2026-07-04 (pre-lab video prep)** — Decisions: **full-race continuous mosaics**
  (not just incident windows); **no fan/segmentation profiles** (that was Ch1).
  Built the pre-lab generation package (one-time, run in Cloud Shell, NOT part of
  the student install): `prelab/probe_camera_labels.sh` (read burned-in labels to
  order cameras by track position), `prelab/normalize_cameras.py` (concat each
  camera's race-spanning CCTV blocks → one aligned 1 FPS clip via HTTPS range
  reads, no bulk download; auto-emits the 6-group config), `prelab/RUNBOOK.md`
  (full workflow + staging to `gs://class-demo/formula-e/r10/mosaics/`),
  `prelab/camera_order.txt` (track-order template, east-loop group confirmed).
  Validated the normalizer's block-overlap/offset math on real filenames (Cam19's
  two race blocks → exactly 2880 s). Demo control: the simulator's `/jump` seeks
  to a flag point — incident race-times documented (hero = ~1680 / 13:32:11).
  **Blocked on Patrick:** run the probe → fill camera_order.txt → run normalize +
  mosaics + upload in Cloud Shell (needs bucket access; I can't from here).
- **2026-07-04 (normalize validated)** — Fixed the CCTV 403 (footage isn't public
  → authenticated HTTPS via `gcloud auth print-access-token`, refreshed per block;
  `--download` fallback added). Patrick ran normalize on the 4 confirmed east-loop
  cams: 4 aligned clips at **2879–2880 s** (full race) + 1 group config. Alignment
  VERIFIED on frame 1691 (13:32:11): Cam18(T12)/Cam19(T13)/Cam20(T14)/Cam21(pit)
  all read 15:32:11–12 — panels time-synced. (Track empty = the known T15 blind
  spot, not an alignment issue.) Pipeline proven end-to-end; remaining work is
  filling all 24 cameras and running the full 6-group generation + upload.

- **2026-07-04 (video plane staged + validated)** — Cameras are numbered in track
  order (Cam01=FL … Cam20=T14, Cam21=pit-entry, Cam22-24=pit lane; no dedicated
  T15 camera → confirms the Fenestraz blind spot). Full 24-camera normalize + 6
  mosaic groups generated and uploaded to `gs://class-demo/formula-e/r10/mosaics/`
  (6 mp4 ≈ 45-52 MB each + manifest.json, 325 MB total). End-to-end VALIDATED:
  Group 1 mosaic at frame 692 (13:15:32) shows labeled, time-aligned panels
  (CAM01-04) with the Günther T1-T2 incident in view. Video plane complete. (Note:
  deleted a stale `grp_01_cam18…` from the earlier 4-cam proof run.) Group→incident
  note: Günther T1-2 is on-camera in Group 1; the Fenestraz hero incident is the
  T15 blind spot (relies on corroboration logic).

- **2026-07-04 (setup ladder)** — Built the student install ladder (borrowed Ch2
  shape, simplified — no BQ/toolbox/subagent; Firestore holds only the "now" doc
  so no composite indexes). `activate.sh` (project/region/venv/ADC preflight/
  Vertex + SIM_URL + MOSAICS_BUCKET), `setup/_lib.sh`, and five numbered steps →
  `deploy/` scripts: 1 enable_apis (lean set), 2 setup_firestore (Native DB only),
  3 deploy_state_writer (worker pool), 4 deploy_simulator, 5 stage_mosaics (create
  `gs://${PROJECT_ID}-fe-mosaics` + copy the 6 mosaics from class-demo).
  `setup/all.sh` runs 1-5 + verify; `verify_checks.py` green-lights three things:
  simulator publishing, Firestore "now" advancing (proves the full
  sim→PubSub→worker→Firestore path), mosaics staged. All scripts pass `bash -n` /
  `py_compile`. **The data layer is now installable in a fresh Qwiklabs project
  with `source activate.sh && bash setup/all.sh`.** (Untested on real GCP — Patrick
  runs it in a lab project to confirm.)

- **2026-07-04 (telemetry observer + lifecycle)** — Built the first agent as an
  on-demand stream consumer, plus the shared lifecycle that keeps agents from
  running 24/7 (Patrick's Gemini-budget requirement). `shared/lifecycle.py`:
  a `Session` with a 10-min deadman cap, graceful SIGTERM/SIGINT stop, and an idle
  watchdog — all four transitions unit-tested. `observers/telemetry/consumer.py`:
  subscribes to fe-telemetry (own sub, SEEK-to-now so only live frames), rolling
  per-car window → deterministic detector → Observations, all under the Session;
  deterministic (no Gemini). Validated offline by replaying the full race through
  `TelemetryObserver` — detects the exact validated set [2,7,17,23,33,48] incl. the
  hero (23+17) and Günther (7). Pub/Sub transport untested (needs GCP). Lifecycle
  note: the video observer (the real Gemini cost) will wear this same Session, and
  the UI will spawn observers on launch + SIGTERM them on exit; the deadman cap is
  the backstop if the UI dies uncleanly.

- **2026-07-04 (lifecycle refined — clock-as-switch)** — Reframed the lifecycle:
  gate Gemini activity on the sim CLOCK, don't cap the cheap parts. `max_runtime_s`
  is now optional (None disables the deadman). The telemetry observer (Gemini-free)
  runs with NO deadman — clock-gated by the idle watchdog (quiet stream → stop) +
  graceful SIGTERM. The deadman is reserved for the video observer (the Gemini
  spender) as a backstop. Plan for the video observer: hold a Live session only
  while `race_time_s` is advancing; if the sim pauses/ends, idle → CLOSE the Live
  session (stop token burn) → reopen when the clock moves. So launching/pausing the
  simulator is itself the on/off switch for Gemini cost.

### UI note (for the console build)
Put a row of buttons along the bottom of the Race Control console that each
`POST /jump` the simulator to ~2 min before a known incident. Candidate incident
list (already have it from the detector; finalize as "definitive"):
  95s (#33), 692s (#7 Günther → 1st SC), 1510s (#2 Vandoorne pit),
  1680s (#23+#17 Fenestraz/Nato — corroborated hero → SC), 1781s (#48 Mortara).
So a "Günther incident" button jumps to ~570s, "Hero incident" to ~1560s, etc.

- **2026-07-04 (video observer — clock-gated, OO)** — Built the Gemini Live video
  observer as clean OO Python (checked current Live API docs: `client.aio.live.
  connect`, turn-based `send_client_content` + `receive`; Vertex model
  `gemini-live-2.5-flash`, env-overridable). Modules: `observers/video/clock.py`
  (SimClock polling /status, advancing detection), `mosaic_source.py` (download +
  1 FPS extract, frame_path(race_second), panel layout from manifest),
  `prompts.py` (grid-aware persona naming each panel's camera_id), `observer.py`
  (`VideoObserver`: CLOCK-GATED Live session — opens while race_time_s advances,
  CLOSES on pause/stall so Gemini cost tracks the sim; deadman backstop, NO
  idle-exit so brief pauses don't kill it; turn-based frame+request cycle isolated
  in `_observe_once` for easy strategy swap). Offline-validated: frame indexing,
  grid prompt, report parsing (pos/neg/garbage), and the clock-gating loop
  (open→observe while advancing → close on stall). Live Gemini call pending GCP.

- **2026-07-05 (live-testing the video observer)** — First live run in a lab
  project surfaced two fixes: (1) mosaic frame extraction needs ffmpeg, which the
  student env lacks → switched to the pip-bundled static binary via `imageio-ffmpeg`
  (`_ffmpeg_bin()` prefers system, falls back to bundled); no apt-get for students.
  (2) The Live model default `gemini-live-2.5-flash` was invalid on Vertex (1008
  policy violation) → corrected to the GA `gemini-live-2.5-flash-native-audio`
  (per the model card: video+image+text in, text out; us-central1 supported;
  10-min default session length matches our deadman). Everything up to the Live
  handshake works, incl. the clock-gate opening the session on the curl jump.
  Watch on next run: whether the native-audio model honours response_modalities
  ["TEXT"] and returns parseable JSON from the turn-based `_observe_once`.

- **2026-07-05 (video observer — pivot off Live API)** — Live testing on Vertex
  proved the Live path is a dead end for us: the only GA Vertex Live model is
  `gemini-live-2.5-flash-native-audio`, which returns `1007 Text output is not
  supported for native audio output model`. The text-capable Live model
  (`gemini-3.1-flash-live-preview`) is Gemini-Developer-API only (needs an API
  key), and Patrick is staying on Vertex/ADC (no key). **Decision: drop the Live
  API for the video observer; use standard multimodal `generate_content` on a
  Vertex vision model (`gemini-2.5-flash`, override `FE_VIDEO_MODEL`).** Same 1 FPS
  clock-paced feed, same Observations, but image-in/JSON-text-out that actually
  works on Vertex — and simpler + cheaper to gate (no persistent session; the
  clock gate just stops issuing calls when race_time_s stalls). Re-validated
  offline: parsing + clock-gating (calls only while advancing, zero when paused).
  Model IDs learned: Vertex Live = native-audio (audio-only); Gemini-API Live =
  `gemini-3.1-flash-live-preview` (`GOOGLE_API_KEY`, no v1beta needed). `media=` is
  deprecated in send_realtime_input (now `video=`) — moot now that we use
  generate_content.

- **2026-07-05 (video observer WORKING live)** — First successful live run: the
  video observer produced real structured incident reports from the Group 1 mosaic
  on Vertex (`gemini-2.5-flash`, generate_content), e.g. "Car #11 stopped in CAM01
  FL, #17 slow behind" / "#17,#25,#11,#37 stopped or stranded in CAM04 T2, some
  facing the wrong way" — per-camera attribution, car numbers, severity, all good.
  Fixed one bug: it replayed from race-second 0 instead of watching 'now' (grabbed
  the whole backlog on startup) → now seeks to the current window each cycle,
  dropping backlog after a /jump (verified: jump→620 processes ~620, not 0).
  Quieted the AFC log noise. Known follow-up: a standing start reads as "stopped
  cars" — add a grid/standing-start exclusion to prompts.py if we ever watch from
  the green flag (mid-race jumps sidestep it). **Both observers now work live.**

- **2026-07-05 (video observer memory)** — Two enhancements to the video observer:
  (1) **10s sliding visual window** (was ~3 new frames) — each call sends the last
  10 race-seconds of frames, overlapping consecutive calls, so the model reasons
  about persistence/motion within one request (`--window`, default 10). (2) A
  **bounded rolling scratchpad** — the last 8 reports are fed into each prompt as
  "your recent reports," and the persona uses them to judge new vs ongoing vs
  clearing (e.g. a flagged car being recovered). Bounded (deque maxlen) so the
  prompt stays flat; the correlator still owns long-term cross-observer continuity.
  Also switched to `gemini-3.5-flash` on the GLOBAL Vertex endpoint (regional 404'd)
  via the shared `make_client()` helper. Offline-validated window + scratchpad.

- **2026-07-05 (correlator wired — full pipeline)** — Wired both observers into
  the correlator via a Pub/Sub **observation bus** (`shared/observation_bus.py`,
  topic `fe-observations`). Observers get a `--publish` flag; the
  **`correlator/service.py`** runtime subscribes (seek-to-now), buffers recent
  Observations (evicted on their own race-time timeline, not wall-clock), fuses
  every 2s, and ANNOUNCES an incident when it's NEW or ESCALATES — drafting the
  report and writing it to Firestore `incidents/` for the console. Escalation
  validated offline: a telemetry-only stop → **DOUBLE YELLOW (new)**, then video
  corroboration → **SAFETY CAR (escalation)**, repeats deduped. Dedup key = cars +
  30s bucket (NOT turn/location, which fill in late). Known edge: if video misreads
  the car number differently from telemetry, the escalation shows as a second NEW
  announcement rather than an escalation — fine functionally (still recommends SC);
  a fuller incident-tracker is a later refinement. Report narrative is the only
  Gemini touch and fires only on new/escalated incidents (cheap). **All three
  agents now run and fuse end to end.**

- **2026-07-05 (correlator live + sensor-latency fix)** — First live correlator run
  worked: telemetry flagged Günther #7 → **DOUBLE YELLOW** with a Gemini-drafted
  report ("uncorroborated by video… confirmation pending"). Surfaced a key design
  insight: the two sensors detect the SAME incident at very different latencies —
  telemetry ~6s (speed→0), video ~73s (it only sees the visible aftermath: stopped
  car + marshals). The 20s correlation window / 60s buffer were far too narrow, so
  they never met. Widened `CORRELATION_WINDOW_S` 20→120 and `BUFFER_S` 60→180;
  re-tested with the real 73s gap → now corroborates and escalates to SAFETY CAR.
  Trade-off noted in code: a very wide window could merge two incidents within
  120s (a location/car-aware correlator is the fuller fix; fine for the
  jump-to-one-incident demo). The incident dedup key uses the stable earliest
  (telemetry) timestamp, so the escalation is correctly recognized as the same
  incident, not a new one.

- **2026-07-05 (persistence escalation + pit guard)** — Removed the dependency on
  the slow/variable video latency for the safety call. New `PROLONGED_STOP` signal:
  the telemetry observer emits it when a stop persists ≥18s, and the fusion flag
  policy escalates a single-source PROLONGED_STOP straight to SAFETY CAR (no video
  wait). Added a **pit-lane guard** — a stop whose GPS is inside an approximate pit
  box (derived from Vandoorne #2's confirmed R10 pit stop) is suppressed, so a long
  pit stop can't trigger a phantom SC (validated: #2 and #33 suppressed; real track
  stops #7/#23/#48 escalate; Nato #17, who got going again, correctly does NOT
  escalate). Correlator announce logic now has three kinds: NEW (double-yellow) →
  ESCALATION (safety-car, telemetry persistence at ~18s) → CONFIRMED (video
  corroborates later, preserving the two-sensor moment without changing the flag).
  Pit box is a FIRST APPROXIMATION (one confirmed pit stop) — refine against the
  official pit geometry. Full timeline validated offline.

- **2026-07-05 (backward-jump bug fixed)** — Live run surfaced a real bug: both
  observers broke on a BACKWARD /jump (the common demo case — sim plays forward
  while you start the agents, then you jump back to an incident). Video tracked
  `_last_processed` and only ran when `now_s > _last_processed`, so after jumping
  back it idled until the replay caught up; telemetry's per-car window still held
  pre-jump samples ("newer" than the post-jump ones), so the detector saw stale
  data. Fixes: telemetry detects a time discontinuity (backward, or forward >5s)
  and clears all per-car state; video re-anchors `_last_processed = now_s-1` and
  clears the scratchpad on a backward jump. Both validated offline (play to 13:18
  → jump back to 685 → Günther stop + escalation detected on the new timeline).

## Build status (what exists now)

- [x] Repo skeleton + packaging
- [x] `shared/models.py` — data contracts
- [x] `observers/telemetry/detector.py` — deterministic trigger (validated)
- [ ] Telemetry Observer agent (characterize a triggered window)
- [x] Video Observer (Gemini Live, 1 FPS) — REBUILT clock-gated + OO (clock/mosaic_source/observer), grid-aware; live inference pending Cloud Shell
- [ ] Ground-truth incident timeline (grading oracle, from RC log + press)
- [x] Correlator / Reporter — fusion + flag policy + report drafting (validated offline)
- [x] Data plane — simulator (→Pub/Sub) + state-writer Worker Pool (→Firestore), borrowed from Ch2
- [x] Video plane — prebuilt 2×2 mosaics GENERATED + staged in class-demo + validated (6 groups, 325 MB)
- [x] setup/ install ladder — activate.sh + 5 numbered steps + all.sh + verify (green-light check), borrowed from Ch2
- [x] Agent lifecycle (shared/lifecycle.py) — deadman timeout / graceful stop / idle watchdog (the 'never 24/7' guarantee)
- [x] Telemetry observer as a stream consumer (subscribe fe-telemetry, seek-to-now → detector → Observations); validated offline
- [x] Video observer (clock-gated, generate_content on Vertex global, 10s window + scratchpad memory) — WORKING live
- [x] Observation bus (Pub/Sub fe-observations) + observers `--publish` + correlator service (subscribe→fuse→announce escalations→Firestore incidents/) — escalation validated offline
- [ ] Race Control console (frontend, one-click approve/reject; reads Firestore incidents/) + incident jump-buttons
- [ ] setup/ ladder (numbered scripts + all.sh + verify) — borrow Ch2
- [ ] Docs (STUDENT_GUIDE, RUN_OF_SHOW, HOW_IT_WORKS, DEMO, architecture.svg)

## Next steps

1. Resolve the open questions above (esp. build target, hero incident, Vision).
2. Draft the architecture diagram (given = grey, build = amber), matching the
   other two hacks' `docs/architecture.svg` convention.
3. Extract the real R10 CCTV clip(s) for the hero window.
4. Scaffold the repo (starter/ + solution/ + setup/ + docs) following the
   established layout.
5. Write STUDENT_GUIDE tiers, RUN_OF_SHOW, HOW_IT_WORKS, DEMO.
