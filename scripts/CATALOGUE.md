# Event catalogue — full-race sweep of all sensors

A one-time, **offline** characterization pass to discover every incident across the
whole race and all six camera groups, so we can pick the demo jump buttons from
real data. It reuses the production detectors but drives them from a plain
race-second loop — **no simulator, no Pub/Sub, no UI, no Cloud Run deploy.**

## Run it (Cloud Shell, in your Qwiklabs project)

```bash
source activate.sh                 # ADC/Vertex + MOSAICS_BUCKET
bash scripts/catalogue_all.sh      # telemetry + all 6 video groups + correlate
```

Then upload the resulting **`catalogue.tar.gz`** (or the individual
`catalogue/*.jsonl` files). We read them together and choose the buttons.

Knobs: `STEP=2 bash scripts/catalogue_all.sh` for finer video coverage (~2.5× the
Gemini calls); default `STEP=5` is ~576 calls/group over the 48-minute race.

## What each piece does

| script | reads | writes | notes |
|---|---|---|---|
| `catalogue_telemetry.py` | bundled `frames.jsonl.gz` (1 Hz) | `catalogue/telemetry.jsonl` | full-race replay through the real `TelemetryObserver`; no external data needed |
| `catalogue_video.py` | the 6 group mosaics in `$MOSAICS_BUCKET` | `catalogue/<group>.jsonl` | full-race Gemini sweep per group; `--all` or `--group <id>` |
| `catalogue_correlate.py` | the JSONL above | `catalogue/incidents.jsonl` + printed table | runs the real fusion policy; ★ marks corroborated (telemetry+video) incidents |

## Output shape

`catalogue/<group>.jsonl` / `telemetry.jsonl` — one observation per line
(`race_time_s`, `ts_utc`, `signal`, `severity`, `confidence`, `camera_id`/`gps`,
`car_numbers`, `summary`).

`catalogue/incidents.jsonl` — one correlated incident per line (`race_time_s`,
`cars`, `corroborated`, `modalities`, `cameras`, `severity`, `flag`, `signals`).

## Run a single group / a time slice

```bash
python scripts/catalogue_video.py --group grp_03_cam09_cam10_cam11_cam12
python scripts/catalogue_video.py --all --start 600 --end 900   # just that window
```
