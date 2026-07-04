# Formula E — The Proactive Race Control Observer

> **Status:** under construction. See **[PLAN.md](PLAN.md)** for the design,
> decisions, and progress log.

An automated, tireless set of eyes for Race Control. Two independent observers
watch the **2024 Berlin E-Prix (Round 10)** as it replays — one reading the live
**CCTV** feed, one reading the live **telemetry** — and a supervising agent
**correlates** their reports, scores severity, drafts a preliminary incident
report, and queues a recommended flag deployment for a human official to approve
with **one click**.

## The three agents

| Agent | Watches | How | Decides |
|---|---|---|---|
| **Video Observer** | CCTV feed | Gemini Live API, ~1 FPS | persistent conditions — stopped car, debris, dust |
| **Telemetry Observer** | 20 Hz telemetry | deterministic trigger → agent | speed→0, hard decel, yaw spike |
| **Correlator / Reporter** | the two observers (via A2A) | fuse within a tolerance window | severity, report, flag recommendation |

The house rule from the other two hacks holds: **deterministic code decides
*when*, the model decides *what*.** The telemetry trigger is the clearest "when";
the model reasoning describes and correlates the "what."

## Repo layout (build in progress)

```
shared/         Pydantic contracts (telemetry samples, observations, incident report).
observers/
  telemetry/    Deterministic detector (the "when") + the characterizing agent.
  video/        Gemini Live 1 FPS observer.
correlator/     The supervising agent — fuse, score, report, recommend.
frontend/       Race Control console (the one-click approve/reject surface).
scripts/        Local test / probe harnesses (run them, don't edit them).
setup/ deploy/  Idempotent provisioning (Pub/Sub, Firestore, Cloud Run, agents).
docs/           Architecture diagram + assets.
```

Media and large data are **not** committed — they live in the `class-demo` GCS
bucket (and locally in `../_video_scratch/`). The hero incident is **Fenestraz
#23 at ~13:32:11 UTC** (retirement stop, corroborated by race control's safety
car at 13:32:28); see PLAN.md.
