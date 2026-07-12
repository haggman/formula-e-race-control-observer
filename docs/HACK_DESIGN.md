# Hack design brief — Challenge 3: Proactive Race Control Observer

**Status:** design settled, ready to execute. This document is the handoff brief.
The application is BUILT and WORKING (that's the reference solution). What remains
is turning it into a hack: the `starter/` + `solution/` split, the notebook, the
setup script, and the docs.

**Audience for this doc:** whoever executes the build (likely a fresh session), and
Patrick as the instructor.

---

## 1. The event

- **Format:** 2–3 hours, ~50 people, working **individually**.
- **Environment:** Qwiklabs → a fresh GCP project per student. Cloud Shell +
  Colab Enterprise + a browser tab for the console.
- **Audience:** decent Python developers. NOT necessarily GCP or GenAI experts.
- **House pattern:** the full stack is given and running; the student builds **one
  focused component** and watches it come to life in a UI they didn't have to write.
- **Not a lab.** Instructions are directional, not step-by-step. Enough that an
  average competent dev can proceed; not so much that they're transcribing.

---

## 2. The student arc (this is the whole design in one paragraph)

**Hour 0.** They run one script. It deploys the data layer, the telemetry observer,
and the console. They open the console and jump to the Günther incident. Telemetry
nails the stop — the car is stationary, it's a confirmed blockage. But the **Video
Agent column is dead**, and Race Control can only offer *"double yellow — pending
video confirmation."* **The system has one sense and knows it.**

**Hour 2.** Their verifier works. The CCTV column narrates what it sees. The
recommendation escalates to **Safety Car · corroborated**, and the Approve button
lights up. **Their code is what authorizes the Safety Car.**

That before/after is the payoff, and every design decision below serves it.

---

## 3. What the student builds

**The Video Verifier** — the Gemini-powered CCTV confirmation of a telemetry stop.
It's the novel part (Gemini reading a `gs://` mosaic *slice* by time offset — no
download, no ffmpeg, no frame extraction) and the most rewarding to watch land.

**The contract we hand them** (given, do not change):

```python
verify(race_time_s: int, *, cars: list[int] | None) -> VideoVerdict
# VideoVerdict.state ∈ {"blocked", "cleared", "unseen", "error"}
```

The correlator already calls this at the right moment with the right arguments.
Their job is everything behind that door.

### The three methods they implement (in `observers/video/verifier.py`)

| Method | What it does | Why it's the lesson |
|---|---|---|
| `_prompt(cams, t, start, end, cars)` | The persistence question + JSON contract | **The heart.** How you pose the question determines whether you can tell a retirement from a spin-and-recover. |
| `VideoVerifier._verify_group(...)` | One Gemini call: `gs://` file + `videoMetadata` start/end offsets | The novel API move. Read a *slice* of a video in the bucket without downloading it. |
| `VideoVerifier._aggregate(per_group, errors)` | Fuse six per-camera-group replies into ONE verdict | Multi-source fusion, and the honesty of `unseen` vs `error`. |

### Everything else in that file is GIVEN

`VideoVerdict` (the contract), `__init__`, `_list_groups`, `_ensure_client`, `_uri`,
`_cams` + `_PANEL_POS` (panel→camera mapping), `_parse` (JSON extraction), `LEAD_S`,
`TAIL_S`, `DEFAULT_MODEL`, `verify()` (orchestration), and the `main()` CLI.

**Keep the CLI.** `python -m observers.video.verifier --at 693 --cars 7` is their
test harness — they can exercise the verifier standalone without the whole stack.
This matters enormously for iteration speed.

**`_sweep()` is given, but SEQUENTIAL** (a plain `for` loop over the six groups,
with a comment noting it's slow). It works; it just takes ~60s. Making it concurrent
is Bonus 1, and they *feel* the improvement.

---

## 4. The one code change that makes their work load-bearing

Today, `fusion.recommend_flag` escalates to Safety Car on a prolonged telemetry stop
alone. That means a working verifier would only *add a column* — a weak payoff.

**Change it so the Safety Car requires corroboration.** In `correlator/fusion.py`:

```python
# BEFORE (current app):
if len(stopped_cars) >= 2 or confirmed_stop or prolonged or incident.severity >= 85:
    return SAFETY_CAR

# AFTER (both starter AND solution):
if confirmed_stop:          # stopped AND (corroborated or video says blocked)
    return SAFETY_CAR
# ...a stop with no video corroboration falls through to DOUBLE_YELLOW
```

Resulting policy — clean, and it's what `fusion.py`'s own docstring has always
claimed the design is (we drifted from it):

```
stop + video "blocked"        → SAFETY CAR
stop, no video corroboration  → DOUBLE YELLOW ("pending video confirmation")
video "cleared" / RECOVERED   → NONE (the false-alarm veto)
pit-lane stop                 → note only, no flag
```

This makes the student's verifier load-bearing across **all three** incident buttons.
It also hands us a bonus for free: *"we just made the system helpless without a
camera — is that acceptable?"* (See Bonus 3, graceful degradation.)

---

## 5. Architecture: given / built / run locally

| Component | Where it lives | Who owns it |
|---|---|---|
| Data layer (Pub/Sub, Firestore, mosaics bucket) | Deployed by script | Given |
| Simulator, State Writer | Cloud Run | Given |
| **Telemetry Observer** | Cloud Run worker pool | Given (working) |
| **Console UI** | Cloud Run service | Given (working) |
| **Correlator (contains their verifier)** | **Runs LOCALLY in Cloud Shell** | **Student** |
| Colab notebook | Colab Enterprise | Student's workbench |

### Why the correlator is NOT deployed

It contains the file they're editing. If it were deployed, every edit would cost a
~5-minute Cloud Build — a brutal loop inside a 2-hour hack. Instead they run:

```bash
python -m correlator.service
```

in one Cloud Shell tab. Edit `verifier.py` → Ctrl-C → rerun → the deployed console
lights up in seconds. **Deploying their correlator to Cloud Run becomes the optional
capstone** ("ship it"), not the dev loop.

Their world: one browser tab with the console, one with the notebook, one Cloud Shell
tab running their agent. Manageable for fifty people.

---

## 6. The setup script (`setup/` — needs REORDERING)

One command. But the ORDER matters, because the notebook needs things long before
the Cloud Run builds finish.

**Required order:**

1. **Print the Colab link FIRST** (before any slow work — see §7).
2. Enable APIs (incl. `aiplatform`).
3. **Provision the Vertex AI service agent + grant it `storage.objectViewer`.**
4. Firestore.
5. **Stage the mosaics** into `gs://${PROJECT_ID}-fe-mosaics`.
6. — *(from here it's slow; the student is already working in the notebook)* —
7. Deploy State Writer, Simulator.
8. Deploy Telemetry Observer, Console. **Do NOT deploy the correlator.**
9. Green-light check.

### ⚠️ Step 3 is critical and currently in the wrong place

When Gemini reads a `gs://` video, the **Vertex AI service agent**
(`service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com`) fetches the
file — *not* the caller's service account. On a fresh project that agent must be
provisioned and granted read on the mosaics bucket, and **provisioning takes several
minutes to propagate.**

Today this lives in `deploy/deploy_correlator.sh` — i.e. dead last, and we're not
even deploying the correlator anymore. **Move it into setup, early.** Otherwise the
first student to run a Gemini cell in the notebook hits:

> `400 FAILED_PRECONDITION: Service agents are being provisioned... please try again in a few minutes.`

We burned an evening on exactly this. Do not make 50 people rediscover it.

---

## 7. The notebook (Colab Enterprise)

**One-click import link** (static — hardcode and `echo` it from the setup script):

```
https://console.cloud.google.com/agent-platform/colab/import/https:%2F%2Fraw.githubusercontent.com%2Fhaggman%2Fformula-e-race-control-observer%2Fmain%2Fnotebooks%2Ffe_video_lab.ipynb
```

**Print it from Cloud Shell, not from the written guide.** Cloud Shell runs *inside*
the Cloud console window, so a link clicked there opens in the same browser session
and project. A link clicked from the instructions (a normal Chrome window) would land
in the wrong session — the console is typically open in incognito. This is a real
trap; the terminal-print sidesteps it entirely.

### What the notebook covers (~20 min, while the stack builds — real work, not filler)

1. **Explore the telemetry.** Load the bundled frames, plot #7's speed, *find the
   stop yourself*. Then look at #33 — and discover a pit stop is **telemetrically
   identical** to a track blockage. That's the whole problem, discovered firsthand.
2. **Dissect the mosaics.** *Script stages, notebook dissects.* Show the raw reality:
   24 CCTV cameras, staggered 30-minute blocks, each starting at a different
   wall-clock time. Then the engineering decision: 24 cameras × one Gemini call each
   = 24 calls per incident; tile them 2×2 and **24 cameras becomes 6 calls.** A 4×
   cost and latency win they can feel. Pull a mosaic into the runtime, extract a
   frame, look at the grid.
3. **Prove the time alignment.** Read the burned-in clock in a frame at mp4 offset
   693 and confirm it equals race-second 693. **Everything rests on this fact** — we
   had to validate it, and so should they.
4. **First Gemini call.** A 60-second `gs://` slice at the Günther stop: *"what do
   you see?"* Raw text comes back. The magic moment.
5. **Iterate toward the persistence prompt.** This *is* Task 2 — done in the fast
   feedback loop of a notebook rather than inside a Cloud Run worker.

**Optional/advanced cell:** build a mosaic from source clips with ffmpeg (tooling
already exists in `prelab/`).

**Do NOT put mosaic staging on the notebook's critical path.** With 50 people,
anything the pipeline depends on that requires a student to correctly run a cell
*will* generate support tickets. The script stages; the notebook explores.

---

## 8. Core tasks (~2h15)

| # | Task | Where | Payoff |
|---|---|---|---|
| 0 | Orientation. Run the script, open the console, jump to Günther. **See the missing sense.** | Console | Feel the gap |
| 1 | Get one camera group's window into Gemini; have it describe the scene. | Notebook | Raw text returns |
| 2 | **Write the persistence prompt + JSON contract.** | Notebook | Structured `blocked`/`cleared` |
| 3 | **Port to `verifier.py`.** Sweep all six groups, aggregate to one verdict. Restart the local correlator. | Cloud Shell | **The board lights up — CORROBORATED** |

### Task 2 is the heart — spend the time here

The question must be posed so that **one** answer separates a retirement from a
spin-and-recover:

> *"By the END of this window, is the racing line still BLOCKED, or did it CLEAR?"*

Not *"is there a stopped car?"* — a car can be stopped at second 5 and gone by second
50. The question is about the **track's state at the end of the window**, not a
car's identity at a moment. Get students to arrive at this themselves; it's the
single best discussion in the hack.

### Acceptance test (give them this)

```bash
python -m observers.video.verifier --at 693 --cars 7      # → blocked, Cam05  (Günther)
python -m observers.video.verifier --at 1698 --cars 17 23 # → blocked, Cam07  (Fenestraz+Nato)
python -m observers.video.verifier --at 1736 --cars 23    # → blocked, Cam07  (Mortara)
```

Then the real test: restart the local correlator, jump to Günther, and watch the
recommendation go from DOUBLE YELLOW to **SAFETY CAR · corroborated**.

---

## 9. The `starter/` + `solution/` split — FOLLOW THE HOUSE PATTERN

⚠️ **Check the sibling hacks first** (`formula-e-race-engineer`,
`formula-e-fan-concierge`). They do NOT duplicate the whole tree. The convention is:

- `starter/<component>/` and `solution/<component>/` are **parallel packages holding
  ONLY the student's component.**
- Everything shared (`frontend/`, `setup/`, `deploy/`, `shared/`, `simulator/`,
  `observers/telemetry/`, `correlator/`) stays at the **top level**, used by both.
- **`activate.sh` selects between them with an env var.** From race-engineer:

  ```bash
  # starter.race_engineer  = the student build (DEFAULT — you work here)
  # solution.race_engineer = the complete reference (the answer key)
  export AGENT_PACKAGE="${AGENT_PACKAGE:-starter.race_engineer}"
  ```
  Students work in `starter/` by default; `export AGENT_PACKAGE=solution...` runs the
  answer key. The deployed demo always runs the solution.

### Applied here

```
starter/video_verifier/verifier.py     <- stubbed  (DEFAULT — the student's file)
solution/video_verifier/verifier.py    <- complete (the answer key)
```

`activate.sh` gains `export VERIFIER_PACKAGE="${VERIFIER_PACKAGE:-starter.video_verifier}"`,
and `correlator/service.py` imports the verifier **dynamically** from that package
instead of the current hardcoded `from observers.video.verifier import VideoVerifier`.
This gives the student a one-env-var escape hatch to the working reference — the
"stuck?" valve the house pattern relies on.

**Note:** `observers/video/` has been emptied of everything else (see §15), so the
verifier is the only thing that moves into the split.

### What to stub in `starter/video_verifier/verifier.py`

- `_prompt()` → stub. Leave the signature, a rich docstring describing what the
  question must achieve, and the JSON schema the rest of the code expects
  (`blockage`, `cleared`, `panel`, `confidence`, `what_you_see`, `seen_car`).
  **This is the main event — scaffold it well but do not write it.**
- `VideoVerifier._verify_group()` → stub. Leave the signature and a TODO naming the
  API surface they need (`types.Part(file_data=..., video_metadata=...)`), plus the
  `_parse` + panel→camera lines as a hint of the expected return shape.
- `VideoVerifier._aggregate()` → stub. Leave the `VideoVerdict` contract and a
  docstring specifying the four states.
- `_sweep()` → **given, but sequential** (see Bonus 1).
- `LIVERIES` → **given but unused** by the starter prompt (Bonus 2 wires it in).

Everything else — including the fusion policy change from §4 — is shared at the top
level and identical for both.

---

## 10. Bonus ladder

1. **Make the sweep concurrent.** Sequential ≈ 60s; `asyncio.gather` ≈ 10s. Measurable,
   satisfying, and a real lesson about I/O-bound fan-out.
2. **Livery hint.** Feed the model a car#→team-livery table so it can identify the
   stopped car — *with a "if you cannot read the number, do not invent one" guard.*
   (We watched it confidently invent numbers without that line.)
3. **Graceful degradation.** We just made the Safety Car depend on a camera. What if
   the camera can't see it? Should the system be paralyzed? Implement: `unseen` +
   a prolonged telemetry stop → escalate anyway. **Best design discussion in the hack.**
4. **The `cleared` verdict.** The false-alarm veto: video sees the car recover, so
   stand the flag down.
5. **Honest error surfacing.** Distinguish "the check RAN and saw nothing" (`unseen`)
   from "the check never ran" (`error`). An outage must not masquerade as an all-clear.
6. **Advanced — correlation bonding.** `fusion._bonds`: stop a lone yaw on one car
   from being swallowed by a *different* car's stop. Meaty, subtle, real.
7. **Capstone — ship it.** Containerize and deploy their correlator to Cloud Run.

---

## 11. Bugs as curriculum (hard-won — do not lose these)

Every one of these was a real bug we hit. They are the best teaching material in the
project: turn them into "why" callouts, discussion prompts, bonus tasks, and
instructor stuck-notes.

| What bit us | The lesson | Where it goes |
|---|---|---|
| We verified a **yaw spike**. The model over-read a harmless spin as a crash — reported a car "stranded against the barrier" that telemetry showed doing 134 km/h. | **Only ask the model to confirm something it can actually confirm.** Gate the expensive call on a signal that warrants it (a *stop*), not a transient. Also: it's cheaper. | Task 3 "why", + a discussion prompt |
| Verifying **at** the stop, before the window played. | *"Confirm from what happened — never peek ahead."* The 50s tail is what lets ONE question separate retirement from recovery. | Task 2 — this IS the lesson |
| A **retired car under tow hit 115 km/h** and falsely "recovered," clearing a Safety Car that should have held. | Thresholds encode assumptions. Tow speed ≠ racing speed. Validate against real data. | Discussion / detector "why" |
| The incident key **included the car list**. Cars accrete → the key changed → duplicate cards AND a duplicate billable Gemini sweep. | **Identity must be stable under growth.** | Instructor note; advanced bonus |
| **Time-only correlation** merged a yaw on #48 into #7's stop 49s later. | Proximity in time ≠ same incident. | Bonus 6 (`_bonds`) |
| A **stationary car is not a blockage** (pit lane). And an *invisible* dismissal looks like a broken app. | Say what you dismissed, and why. | Task 0 (#33 button) |
| A publisher died silently on `create_topic`; `publish()` futures swallowed errors. Two columns stayed blank while logs said "CONFIRMED." | **Silent failure is the enemy.** Make failures loud. | Instructor stuck-note |
| Fresh-project **Vertex service agent** wasn't provisioned/granted → `FAILED_PRECONDITION` on every `gs://` read. | Managed services have identities of their own. | §6 — solved in setup |
| **Stale state across a jump** — the observer reset, the correlator didn't; old observations bled into the next run. | Replayable systems need explicit reset semantics. | Instructor note |

### Predicted stuck-points (put these in the instructor guide)

- *"My Gemini call 400s on the gs:// file"* → Vertex service agent / bucket grant.
- *"It says no CCTV view"* → check the `videoMetadata` offsets and the group list.
- *"It hallucinated a crash"* → verifying too early, or verifying a non-stop.
- *"It's really slow"* → sequential sweep. That's Bonus 1.
- *"The flag never reaches Safety Car"* → their verdict isn't returning `blocked`,
  or the correlator wasn't restarted after the edit.

---

## 12. Run-of-show beats

1. **Open with the finished thing.** Jump to Günther on the instructor's build: stop
   → confirmed → Safety Car. *"You're going to build the eye."*
2. **The data.** 24 cameras, one race, and a question that's harder than it looks:
   a stopped car and a pit stop are identical in telemetry.
3. **Kick off the setup script** (it prints the Colab link) → straight into the notebook.
4. **Notebook**: explore, prove the alignment, first Gemini call.
5. **Task 2 — the question.** The best discussion of the day. Let them argue about it.
6. **Task 3 — the port.** The board lights up. **The moment.**
7. **Bonuses**, self-paced.
8. **Close on the thesis:** *deterministic code decides WHEN to spend a model call;
   the model decides WHAT it's looking at.* That's why this runs on a handful of
   Gemini calls per race instead of streaming video continuously.

---

## 13. Deliverables for the executing session

1. `starter/` and `solution/` (per §9).
2. `notebooks/fe_video_lab.ipynb` (per §7) — committed to `main`, public.
3. Reordered `setup/` (per §6), incl. the Vertex service-agent move and the Colab
   link print. **Correlator excluded from deploy.**
4. `docs/STUDENT_GUIDE.md` — directional, not step-by-step. Tasks, the "why"
   callouts from §11, the acceptance tests.
5. `docs/RUN_OF_SHOW.md` — §12, with timings and the stuck-points table.
6. Apply the fusion policy change (§4) to both trees.

---

## 15. Repo hygiene — done, and still owed

### Done (before the handoff)

`observers/video/` — the exact folder students work in — was full of the **retired
streaming-observer chain** from the design we abandoned. Worst of all, it contained
a file called **`prompts.py`**: Task 2 tells a student "write the prompt," and there
was a `prompts.py` sitting right there in their folder. That's fifty support tickets.

Retired (recoverable from git history):
- `observers/video/observer.py` (299 lines — the old Gemini-Live streaming observer)
- `observers/video/prompts.py` (its prompts — the booby trap)
- `observers/video/mosaic_source.py`, `observers/video/frame_source.py` (the old
  download-and-extract-frames approach; the verifier reads `gs://` slices directly)
- `scripts/catalogue_video.py` (the only consumer of the above)

Moved:
- `observers/video/clock.py` → **`shared/clock.py`** (it's the sim clock, used by the
  correlator — nothing to do with the video observer). Import rewired; verified.
- `notebooks/{build_camera_mosaics.py, build_frames.py, camera_groups.example.json}`
  → **`prelab/`** (instructor one-time tooling; `notebooks/` is now for the student).

Result: `observers/video/` contains **only `verifier.py`** — precisely the thing that
moves into the `starter/` + `solution/` split.

### ⚠️ Still owed — Patrick must run these locally

The sandbox could not delete cloud-synced files. Run in the repo root:

```bash
rm -f .git/index.lock            # stale lock
git rm -f observers/video/observer.py observers/video/prompts.py \
          observers/video/mosaic_source.py observers/video/frame_source.py \
          observers/video/clock.py scripts/catalogue_video.py
git add -A && git commit -m "Retire the streaming video observer chain; move SimClock to shared/"
```

(`shared/clock.py` already exists and the import is already rewired — these deletes
just remove the now-orphaned originals.)

### Also owed

- **`README.md` is stale and actively misleading.** It still describes the abandoned
  architecture — *"Video Observer — Gemini Live API, ~1 FPS"* and A2A wiring — and is
  marked "under construction." It is the first thing a student (and the next session)
  reads. **Rewrite it** to describe the system as built: telemetry observer + a
  video verifier that reads `gs://` mosaic slices on demand, fused by a deterministic
  correlator.
- `notebooks/verify_camera_mapping.ipynb` is our validation notebook. It's a strong
  **seed** for `fe_video_lab.ipynb` (it already has project auto-detect, the Gemini
  client, the camera groups, and the alignment check). Build the student notebook
  from it, then retire it.

---

## 14. Open items

- [ ] Time the reordered setup script end-to-end on a genuinely fresh project. Target:
      data + Gemini usable in the notebook within ~3 minutes; full stack < 20.
- [ ] `deploy/verify_app.sh` currently checks for the correlator heartbeat — it must
      tolerate the correlator being absent (it now runs locally).
- [ ] Decide whether the notebook's ffmpeg mosaic-build cell ships or stays cut.
