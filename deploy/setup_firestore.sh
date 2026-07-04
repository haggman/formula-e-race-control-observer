#!/usr/bin/env bash
# Provision the Firestore Native-mode database for the "now" store.
#
# Simpler than the Ch2 setup: this hack only writes ONE doc per race —
# race_states/{race_id}, overwritten every second — for the Race Control console's
# live view. There are no event-history queries, so NO composite indexes are
# needed. Just the Native DB.
#
# Idempotent: safe to re-run. Required env: PROJECT_ID, REGION (source activate.sh).
set -euo pipefail

[[ -n "${PROJECT_ID:-}" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }
[[ -n "${REGION:-}" ]] || { echo "ERROR: REGION required (source activate.sh)" >&2; exit 1; }

FIRESTORE_LOCATION="${FIRESTORE_LOCATION:-${REGION}}"
DATABASE_ID="${FIRESTORE_DATABASE_ID:-(default)}"

echo "=================================================================="
echo "Project:  $PROJECT_ID   Location: $FIRESTORE_LOCATION   DB: $DATABASE_ID"
echo "=================================================================="

gcloud services enable firestore.googleapis.com --project="$PROJECT_ID"

if gcloud firestore databases describe --database="$DATABASE_ID" \
        --project="$PROJECT_ID" >/dev/null 2>&1; then
    TYPE="$(gcloud firestore databases describe --database="$DATABASE_ID" \
        --project="$PROJECT_ID" --format='value(type)')"
    echo "    Database '$DATABASE_ID' already exists (type=$TYPE)"
    [[ "$TYPE" == "FIRESTORE_NATIVE" ]] || echo "    WARN: not Native mode; the code assumes Native."
else
    echo ">>> Creating Native-mode database in $FIRESTORE_LOCATION..."
    gcloud firestore databases create --database="$DATABASE_ID" \
        --location="$FIRESTORE_LOCATION" --type=firestore-native --project="$PROJECT_ID"
    echo "    created"
fi
echo ">>> Firestore ready (single 'now' doc, no indexes needed)."
