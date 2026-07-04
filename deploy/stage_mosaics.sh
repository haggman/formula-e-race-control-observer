#!/usr/bin/env bash
# Stage the prebuilt camera mosaics into the STUDENT project, so the video
# observer streams from a bucket the student owns (not the shared class bucket).
#
# Copies the 6 mosaics + manifest from gs://class-demo/formula-e/r10/mosaics/
# into gs://${MOSAICS_BUCKET} (default ${PROJECT_ID}-fe-mosaics).
#
# Idempotent: re-running re-syncs. Required env: PROJECT_ID, REGION, MOSAICS_BUCKET
# (source activate.sh).
set -euo pipefail

[[ -n "${PROJECT_ID:-}" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }
SRC="${MOSAICS_SRC:-gs://class-demo/formula-e/r10/mosaics}"
DEST_BUCKET="${MOSAICS_BUCKET:-${PROJECT_ID}-fe-mosaics}"
DEST="gs://${DEST_BUCKET}/mosaics"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Source:  $SRC"
echo "Dest:    $DEST"
echo "=================================================================="

echo ">>> Ensuring destination bucket exists..."
if ! gcloud storage buckets describe "gs://${DEST_BUCKET}" --project="$PROJECT_ID" >/dev/null 2>&1; then
    gcloud storage buckets create "gs://${DEST_BUCKET}" \
        --project="$PROJECT_ID" --location="$REGION" --uniform-bucket-level-access
    echo "    created gs://${DEST_BUCKET}"
else
    echo "    bucket gs://${DEST_BUCKET} exists"
fi

echo ">>> Copying mosaics + manifest..."
gcloud storage cp "${SRC}/*.mp4" "${SRC}/manifest.json" "${DEST}/" --project="$PROJECT_ID"

echo ""
echo ">>> Staged. Contents:"
gcloud storage ls -l "${DEST}/" --project="$PROJECT_ID"
COUNT="$(gcloud storage ls "${DEST}/" --project="$PROJECT_ID" | grep -c '\.mp4$' || true)"
echo ""
echo ">>> ${COUNT} mosaic(s) staged in gs://${DEST_BUCKET}. The video observer"
echo "    reads MOSAICS_BUCKET; a group streams from ${DEST}/<group_id>.mp4."
