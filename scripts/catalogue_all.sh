#!/usr/bin/env bash
# Full offline catalogue: telemetry + all 6 video groups + correlate, then bundle
# the results for upload. Run from the repo root after `source activate.sh`
# (needs ADC/Vertex + MOSAICS_BUCKET). Skips the UI, the simulator, and Pub/Sub.
#
#   bash scripts/catalogue_all.sh              # step=5s (default, ~576 calls/group)
#   STEP=2 bash scripts/catalogue_all.sh       # finer coverage, ~2.5x the calls
#
# Produces ./catalogue/*.jsonl and ./catalogue.tar.gz — upload the tarball.
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="${OUT:-catalogue}"
STEP="${STEP:-5}"
WINDOW="${WINDOW:-10}"
mkdir -p "$OUT"

echo "== 1/3  telemetry catalogue =="
python scripts/catalogue_telemetry.py --out "$OUT"

echo "== 2/3  video catalogue (all groups, step=${STEP}s) =="
python scripts/catalogue_video.py --all --step "$STEP" --window "$WINDOW" --out "$OUT"

echo "== 3/3  correlate =="
python scripts/catalogue_correlate.py --dir "$OUT"

tar -czf catalogue.tar.gz "$OUT"
echo
echo "DONE — bundled $(find "$OUT" -name '*.jsonl' | wc -l) file(s) into catalogue.tar.gz"
echo "Upload catalogue.tar.gz (or the individual $OUT/*.jsonl files)."
