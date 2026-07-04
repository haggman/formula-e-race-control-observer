#!/usr/bin/env bash
# Deploy the telemetry simulator to Cloud Run (borrowed from the Ch2 simulator).
# Frames are BUNDLED in the image (simulator/src/frames.jsonl.gz), so there's no
# GCS frames dependency. Idempotent: creates the topic + SA + grants and deploys.
#
# Required: a project set via `gcloud config set project`, or PROJECT_ID env.

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-fe-simulator}"
REGION="${REGION:-us-central1}"
TOPIC_NAME="${TOPIC_NAME:-fe-telemetry}"
SA_NAME="${SA_NAME:-fe-simulator-sa}"
RACE_ID="${RACE_ID:-berlin_2024_r10}"
REPLAY_SPEED_MULTIPLIER="${REPLAY_SPEED_MULTIPLIER:-1.0}"
AUTO_RESTART="${AUTO_RESTART:-false}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "$PROJECT_ID" ]]; then
    echo "ERROR: no project set. Run 'gcloud config set project YOUR_PROJECT'." >&2
    exit 1
fi
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Service: $SERVICE_NAME   Topic: $TOPIC_NAME   SA: $SA_EMAIL"
echo "Frames:  bundled in image (1 Hz Berlin R10)"
echo "=================================================================="

echo ">>> Enabling APIs..."
gcloud services enable run.googleapis.com pubsub.googleapis.com \
    cloudbuild.googleapis.com artifactregistry.googleapis.com --project="$PROJECT_ID"

echo ">>> Waiting for Cloud Run API to settle..."
for attempt in 1 2 3 4 5 6; do
    gcloud run services list --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 && break
    echo "    ...Run API not serving yet — retry ${attempt}/6 in 10s"; sleep 10
done

echo ">>> Ensuring Pub/Sub topic exists..."
gcloud pubsub topics describe "$TOPIC_NAME" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud pubsub topics create "$TOPIC_NAME" --project="$PROJECT_ID"

echo ">>> Ensuring service account exists..."
gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Formula E Simulator" --project="$PROJECT_ID"

echo ">>> Granting roles/pubsub.publisher (retry for new-SA propagation)..."
granted=0
for attempt in 1 2 3 4 5 6; do
    if gcloud pubsub topics add-iam-policy-binding "$TOPIC_NAME" \
        --member="serviceAccount:${SA_EMAIL}" --role="roles/pubsub.publisher" \
        --project="$PROJECT_ID" >/dev/null 2>&1; then granted=1; break; fi
    echo "    ...IAM can't see ${SA_EMAIL} yet — retry ${attempt}/6 in 10s"; sleep 10
done
[[ "$granted" == "1" ]] || { echo "ERROR: could not grant pubsub.publisher" >&2; exit 1; }

echo ">>> Deploying Cloud Run service..."
gcloud run deploy "$SERVICE_NAME" \
    --source=. --quiet --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" --allow-unauthenticated \
    --min-instances=1 --max-instances=1 --cpu=1 --memory=512Mi \
    --no-cpu-throttling --concurrency=10 --timeout=3600 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},PUBSUB_TOPIC=${TOPIC_NAME},RACE_ID=${RACE_ID},REPLAY_SPEED_MULTIPLIER=${REPLAY_SPEED_MULTIPLIER},AUTO_RESTART=${AUTO_RESTART}"

URL=""
for attempt in 1 2 3 4 5 6; do
    URL="$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' --quiet 2>/dev/null || true)"
    [[ -n "$URL" ]] && break
    echo "    ...deployed, describe not serving yet — retry ${attempt}/6 in 10s"; sleep 10
done
[[ -n "$URL" ]] || { echo "ERROR: deployed but URL unreadable — rerun (idempotent)." >&2; exit 1; }

echo ""
echo "=================================================================="
echo "Deployed!  URL: $URL"
echo "Status:    curl ${URL}/status"
echo "Schema:    curl ${URL}/schema"
echo "=================================================================="
