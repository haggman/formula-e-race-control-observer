# Deployment runbook — data layer → application

End-to-end deploy of the Proactive Race Control Observer into a **fresh Google
Cloud project**, from an empty project to a live Race Control console you can
drive. Written to be followed top to bottom in Cloud Shell. Everything is
idempotent — if a step fails, fix the cause and re-run; completed work
fast-forwards.

Budget: ~15 min for the data layer, ~10 min for the application layer.

---

## What you're deploying

Six things, in two tiers. The **data layer** (provisioned by `setup/`) is the
running race and its plumbing; the **application layer** (provisioned by
`deploy/`) is the three agents that watch it.

```
                       ┌─────────────── DATA LAYER (setup/) ───────────────┐
  frames.jsonl.gz ──▶  Simulator ──▶  fe-telemetry ──▶  State Writer ──▶ Firestore
   (bundled)          (Cloud Run svc)   (Pub/Sub)      (worker pool)    race_states/
                                            │
                       ┌──────────── APPLICATION LAYER (deploy/) ──────────┐
                       ▼
             Telemetry Observer ──▶ fe-observations ──▶ Correlator ──▶ fe-incidents
              (worker pool)          (Pub/Sub)          (worker pool)    (Pub/Sub)
                                          ▲                  │                │
                        (verifier video reads) ──────────────┘                ▼
                                                        Vertex Gemini    Race Control
                                                        + gs:// mosaics  Console (svc) ──▶ browser
```

- **Simulator** (service) — replays Berlin R10 at 1 Hz to `fe-telemetry`; exposes
  `/status`, `/jump`, `/pause`, `/resume`, `/restart`.
- **State Writer** (worker pool) — writes the latest frame to Firestore
  `race_states/` for the console's live car layer.
- **Telemetry Observer** (worker pool) — the deterministic detector; publishes
  Observations to `fe-observations`.
- **Correlator** (worker pool) — fuses observations, verifies stops on CCTV with
  Vertex Gemini, publishes fused recommendations to `fe-incidents`.
- **Console** (service) — subscribes to both buses, renders the UI, and drives the
  simulator. The one URL you open.

---

## Prerequisites

- A Google Cloud **project with billing enabled**, and `Owner` (or enough to
  create service accounts, grant project IAM, and deploy Cloud Run).
- **Cloud Shell** (recommended — `gcloud`, Docker build, and ADC are already set
  up). Any machine with `gcloud` + application-default credentials works too.
- Read access to the shared class bucket **`gs://class-demo`** (holds the prebuilt
  camera mosaics the video verifier streams). You already own it; a fresh project
  just needs your user to be able to read it during staging.
- The Cloud Run **worker pools** feature (already used by the State Writer, so the
  project pattern is proven).

---

## Step 0 — Clone and activate

```bash
git clone <YOUR_REPO_URL> formula-e-race-control-observer
cd formula-e-race-control-observer

gcloud config set project YOUR_NEW_PROJECT_ID
source activate.sh
```

`activate.sh` sets `PROJECT_ID` / `REGION`, builds the `.venv`, wires the Vertex
env, and — once the data layer is up — auto-discovers and caches `SIM_URL` and
`MOSAICS_BUCKET` in `.env.local`. Re-`source` it any time; it's idempotent. If it
warns that Application Default Credentials look stale, run
`gcloud auth application-default login` and re-source.

---

## Part A — Data layer

One command provisions APIs, Firestore, the State Writer, the Simulator, and the
mosaics, then green-light checks the result:

```bash
bash setup/all.sh
```

That runs, in order (each is also runnable on its own and is idempotent):

1. `setup/1_enable_apis.sh` — enable Run, Pub/Sub, Firestore, Cloud Build,
   Artifact Registry, Vertex AI, Storage.
2. `setup/2_setup_firestore.sh` — Firestore in Native mode (the "now" store).
3. `setup/3_deploy_state_writer.sh` — the State Writer worker pool.
4. `setup/4_deploy_simulator.sh` — the telemetry simulator service.
5. `setup/5_stage_mosaics.sh` — copy the 6 mosaics + manifest from
   `gs://class-demo/...` into `gs://${PROJECT_ID}-fe-mosaics`.

It finishes with `setup/verify.sh` (a green-light check) and prints the simulator
URL. Re-run `bash setup/verify.sh` any time to re-check.

**Confirm the data layer is live** before moving on:

```bash
source activate.sh                 # picks up cached SIM_URL from .env.local
curl -s "$SIM_URL/status" | head   # should return race_time_s + phase
gcloud storage ls "gs://${MOSAICS_BUCKET}/mosaics/" | grep -c '\.mp4$'   # expect 6
```

---

## Part B — Application layer

With the data layer up (the simulator especially — the next two agents discover
its URL for the race clock and control plane), deploy the three agents:

```bash
bash deploy/deploy_app.sh
```

That runs, in order:

1. `deploy/deploy_telemetry_observer.sh` — worker pool `fe-telemetry-observer`.
   Pulls `fe-telemetry`, publishes `fe-observations`. SA:
   `pubsub.editor` + `datastore.user`.
2. `deploy/deploy_correlator.sh` — worker pool `fe-correlator`. Subscribes
   `fe-observations`, runs the Vertex video verifier against the mosaics, publishes
   `fe-incidents`. SA adds `aiplatform.user` + `storage.objectViewer`; env carries
   `SIM_URL` and `MOSAICS_BUCKET`.
3. `deploy/deploy_console.sh` — service `fe-console` (always-on, unauthenticated).
   **Prints the Console URL** at the end.

It finishes with `deploy/verify_app.sh` (a green-light check, below) — the same way
`setup/all.sh` ends with `setup/verify.sh`.

Each script builds its image with Cloud Build into the shared `fe-services`
Artifact Registry repo, creates its service account, and grants IAM with
propagation retries. You can run any one on its own to redeploy just that agent.

> **Why these roles:** the observers, correlator, and console each create and
> *seek* their own Pub/Sub subscriptions at startup (live data only), which needs
> more than plain `subscriber`, so they get `pubsub.editor`. Pragmatic for the
> hack; tighten later if desired.

**Confirm the agents are healthy** — the one-command green-light check (also run
automatically as the last step of `deploy_app.sh`):

```bash
bash deploy/verify_app.sh
```

It polls each agent's Firestore heartbeat (`agent_status/telemetry`, `correlator`,
`video`) and pings the console URL — a fresh heartbeat proves the process is
actually running and reachable, not merely deployed. Each failing check prints a
one-line fix. To dig into a specific agent:

```bash
gcloud run worker-pools logs read fe-telemetry-observer --region "$REGION" --limit 20
gcloud run worker-pools logs read fe-correlator          --region "$REGION" --limit 20
```

You should see the observer "online — pulling", and the correlator "online —
fusing observations" with "video verifier armed".

---

## Part C — Drive the demo

1. Open the **Console URL** (from the end of Part B) in a browser. The top bar
   shows the race clock and flag state; the three columns are the Telemetry Agent,
   Video Agent, and Race Control recommendations. The agent status dots should read
   **online**.
2. Use the **Jump** buttons along the bottom to seek the simulator to a scripted
   incident, then **Resume**. Each seeks in, plays the incident window, and the
   server auto-pauses at the end:
   - **Günther (corroborated)** — a real retirement: telemetry stop → prolonged →
     Cam05 confirms → **Safety Car holds** (the car is out; the recovery truck's
     tow speed does not clear it).
   - **Fenestraz (telemetry-only)** — a stop with no clean camera → single-sensor
     Safety Car recommendation.
   - **Mortara (stop)** — a genuine stop that later **recovers** at racing speed →
     the flag stands down.
   - **#33 (pit — no flag)** — a pit stop the detector correctly ignores.
3. Approve or reject a recommendation with the one-click buttons; the decision is
   recorded to Firestore `incidents/`.

If a card looks wrong or you want a clean slate, use **Clear** / **Restart** in the
console.

---

## Teardown

To dismantle the whole thing (or just the app tier before a rebuild):

```bash
REGION="${REGION:-us-central1}"
# Application tier
gcloud run worker-pools delete fe-telemetry-observer --region "$REGION" --quiet
gcloud run worker-pools delete fe-correlator          --region "$REGION" --quiet
gcloud run services     delete fe-console             --region "$REGION" --quiet
# Data tier
gcloud run worker-pools delete fe-state-writer        --region "$REGION" --quiet
gcloud run services     delete fe-simulator           --region "$REGION" --quiet
# Optional: buckets, topics, subscriptions, Firestore data, service accounts
gcloud storage rm -r "gs://${PROJECT_ID}-fe-mosaics" --quiet
```

The cleanest reset is to delete the whole project.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Console dots never turn **online** | Agents can't reach Firestore/Pub/Sub | Check `gcloud run worker-pools logs read ...`; re-run the agent's deploy script (IAM may still have been propagating). |
| Recommendations never appear | Observer not publishing, or correlator not subscribed | Confirm the observer log shows "pulling" and the correlator shows "fusing"; both must be `--instances=1` and running. |
| Video Agent stays empty on a stop | Correlator lacks `SIM_URL`, Vertex, or mosaics access | Ensure the simulator is deployed *before* the correlator; check `aiplatform.user` + `storage.objectViewer`; confirm `gs://${MOSAICS_BUCKET}/mosaics/` has 6 mp4s. |
| Verifier fires too early / wrong window | `SIM_URL` was unset at correlator deploy | Redeploy the correlator after the simulator exists (`bash deploy/deploy_correlator.sh`). |
| Jump/Resume buttons do nothing | Console has no `SIM_URL` | Redeploy the console after the simulator exists (`bash deploy/deploy_console.sh`). |
| `worker-pools: command not found` / flag errors | Worker pools use `--instances=N`, **not** `--min/--max-instances` | Update gcloud (`gcloud components update`); the scripts already use the right flags. |
| Mosaic staging fails | No read on `gs://class-demo` | Ensure your Cloud Shell identity can read the source bucket, then re-run `setup/5_stage_mosaics.sh`. |

---

## One-page quick reference

```bash
# From repo root, in Cloud Shell:
gcloud config set project YOUR_NEW_PROJECT_ID
source activate.sh
bash setup/all.sh          # data layer  (~15 min), ends with setup/verify.sh
source activate.sh         # pick up cached SIM_URL
bash deploy/deploy_app.sh  # application  (~10 min), ends with deploy/verify_app.sh → prints Console URL
# Re-check any time:  bash setup/verify.sh   /   bash deploy/verify_app.sh
# Open the Console URL, hit a Jump button, then Resume.
```
