# Pre-lab video prep — build the 2×2 mosaics and stage them in class-demo

**One-time, before the event. Not part of the student install.** Run in Cloud
Shell (or anywhere with `gcloud` auth + read on `gs://class-demo` and write to the
mosaics folder). Produces the tiny prebuilt mosaics the video observer streams.

The output lands in `gs://class-demo/formula-e/r10/mosaics/` — a `manifest.json`
plus one small mp4 per camera group. Students copy their group into their own
project at install time (see the student `setup/` ladder).

## Step 1 — Confirm camera → track position

Camera track positions come from each camera's burned-in label ("T13 - CAM19").
Read them once:

```bash
bash prelab/probe_camera_labels.sh          # → /tmp/cam_labels/camera_labels_sheet.jpg
```

Record camera → turn, then order the 24 cameras by track position and group them
into six 2×2 sets of four physically-consecutive cameras (travel order
TL→TR→BL→BR). Confirmed so far: **Cam18=T12, Cam19=T13, Cam20=T14, Cam21=pit/T15**
(the east-loop group). Fill the rest into `notebooks/camera_groups.full.json`.

## Step 2 — Normalize each camera to the race window (full-race coverage)

The CCTV blocks are staggered (each camera's 30-min blocks start at different
local times), so we first normalize every camera to ONE aligned 1 FPS clip
spanning the whole race (13:04:00–13:52:00 UTC). Then every mosaic panel lines up
at offset 0.

Put the 24 camera IDs in **track order** (from Step 1) into
`prelab/camera_order.txt`, one per line, optionally `CamXX,LABEL`. Then:

```bash
python prelab/normalize_cameras.py --order prelab/camera_order.txt
```

This reads each block directly from the bucket over HTTPS with ffmpeg range seeks
(gs://class-demo is public-read), so it only transfers the needed 1 FPS segments
— **no 120 GB of downloads, no Cloud Shell disk pressure**. Add `--auth` to use
signed URLs if your bucket isn't public. It writes `cam_xx_1fps.mp4` per camera
**and** auto-emits `notebooks/camera_groups.full.json` (consecutive fours → 2×2
groups, offset 0, start_utc = race start).

## Step 3 — Generate the mosaics

```bash
python notebooks/build_camera_mosaics.py notebooks/camera_groups.full.json /tmp/mosaics
```

Produces `/tmp/mosaics/<group_id>.mp4` (2×2, 1 FPS, labeled panels) + `manifest.json`
— ~58 MB per group, ~350 MB for all six.

## Step 4 — Stage in class-demo

```bash
gcloud storage cp /tmp/mosaics/*.mp4 /tmp/mosaics/manifest.json \
    gs://class-demo/formula-e/r10/mosaics/
```

That's it — the video plane is now a set of tiny artifacts alongside the telemetry
frames (`gs://class-demo/formula-e/r10/simulator/…`), ready for the student
install scripts to pull.

## Demo control — jump to a flag point

At lab time, seek the simulator straight to an incident so the detector fires on
cue (the simulator's `/jump` endpoint, borrowed from Ch2):

```bash
curl -X POST "$SIM_URL/jump" -H 'content-type: application/json' -d '{"race_time_s": 1680}'
```

Incident race-times (seconds since green flag, 13:04:00 UTC):

| race_time_s | UTC      | what                                         |
|-------------|----------|----------------------------------------------|
| 95          | 13:05:35 | #33 stopped (early)                          |
| 692         | 13:15:32 | #7 Günther stop → 1st Safety Car             |
| 1510        | 13:29:10 | #2 Vandoorne stop (really a pit stop)        |
| **1680–1691** | **13:32:11** | **#23 Fenestraz + #17 Nato — the corroborated hero incident** |
| 1781        | 13:33:41 | #48 Mortara stop                             |

Jump to ~1680 to demo the hero incident (both observers agree → Safety Car).
