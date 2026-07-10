#!/usr/bin/env bash
# Deploy the Race Control Console as a Cloud Run SERVICE (HTTP + WebSocket).
#
# It subscribes to BOTH buses (fe-observations for the raw sensor feeds,
# fe-incidents for the fused recommendations), polls Firestore race_states /
# agent_status, records approvals in incidents/, and drives the simulator over
# HTTP (jump / pause / resume / restart). This is the ONE piece with a request
# surface, so it's a regular service (not a worker pool).
#
# Always-on single instance (--min-instances=1 --max-instances=1 --no-cpu-throttling):
# the live Pub/Sub subscriptions + WebSocket fan-out must keep running between
# requests, and a single instance keeps every browser on the same subscriber.
#
# Required: PROJECT_ID and REGION (source activate.sh), or a gcloud project set.

set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-fe-console}"
SA_NAME="${SA_NAME:-fe-console-sa}"
REGION="${REGION:-us-central1}"
REPO_NAME="${REPO_NAME:-fe-services}"
RACE_ID="${RACE_ID:-berlin_2024_r10}"
SIMULATOR_SERVICE="${SIMULATOR_SERVICE:-fe-simulator}"
OBSERVATIONS_TOPIC="${OBSERVATIONS_TOPIC:-fe-observations}"
INCIDENTS_TOPIC="${INCIDENTS_TOPIC:-fe-incidents}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

SIM_URL="${SIM_URL:-$(gcloud run services describe "$SIMULATOR_SERVICE" \
    --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)}"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Service: $SERVICE_NAME   (HTTP + WebSocket, always-on)"
echo "Sim:     ${SIM_URL:-(unset!)}   Race: $RACE_ID   SA: $SA_EMAIL"
echo "=================================================================="
[[ -n "$SIM_URL" ]] || echo "WARNING: ${SIMULATOR_SERVICE} URL not found — the console's Jump/Resume controls won't work until SIM_URL is set (deploy the simulator first)."

echo ">>> Enabling APIs..."
gcloud services enable run.googleapis.com pubsub.googleapis.com \
    firestore.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    --project="$PROJECT_ID"

echo ">>> Waiting for Cloud Run API to settle..."
for attempt in 1 2 3 4 5 6; do
    gcloud run services list --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 && break
    echo "    ...retry ${attempt}/6 in 10s"; sleep 10
done

echo ">>> Ensuring service account..."
gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Formula E Race Control Console" --project="$PROJECT_ID"

echo ">>> Granting roles (Pub/Sub pull+seek both buses, Firestore read/write; retry for propagation)..."
for role in roles/pubsub.editor roles/datastore.user; do
    granted=0
    for attempt in 1 2 3 4 5 6; do
        if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:${SA_EMAIL}" --role="$role" \
            --condition=None --quiet >/dev/null 2>&1; then granted=1; break; fi
        echo "    ...IAM can't see ${SA_EMAIL} yet — retry ${attempt}/6 in 10s"; sleep 10
    done
    [[ "$granted" == "1" ]] || { echo "ERROR: failed to grant $role" >&2; exit 1; }
    echo "    granted $role"
done

echo ">>> Ensuring Pub/Sub topics (console self-creates + seeks its own subscriptions)..."
for topic in "$OBSERVATIONS_TOPIC" "$INCIDENTS_TOPIC"; do
    gcloud pubsub topics describe "$topic" --project="$PROJECT_ID" >/dev/null 2>&1 \
        || gcloud pubsub topics create "$topic" --project="$PROJECT_ID"
done

echo ">>> Building image with Cloud Build..."
gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "$REPO_NAME" --location="$REGION" \
        --repository-format=docker --description="Formula E services" --project="$PROJECT_ID"

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
CB_BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/logging.logWriter roles/artifactregistry.writer roles/storage.admin; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${CB_BUILD_SA}" --role="$role" \
        --condition=None --quiet >/dev/null 2>&1 || true
done
sleep 5

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/console:$(date -u +%Y%m%d-%H%M%S)"
CB_CONFIG="$(mktemp)"
cat > "$CB_CONFIG" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '${IMAGE}', '-f', 'frontend/Dockerfile', '.']
images: ['${IMAGE}']
EOF
gcloud builds submit "$REPO_ROOT" --config="$CB_CONFIG" --project="$PROJECT_ID"
rm -f "$CB_CONFIG"

echo ">>> Deploying Cloud Run service (always-on, unauthenticated)..."
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},RACE_ID=${RACE_ID}"
[[ -n "$SIM_URL" ]] && ENV_VARS="${ENV_VARS},SIM_URL=${SIM_URL}"
gcloud run deploy "$SERVICE_NAME" \
    --image="$IMAGE" --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" --allow-unauthenticated \
    --min-instances=1 --max-instances=1 --cpu=1 --memory=512Mi \
    --no-cpu-throttling --concurrency=80 --timeout=3600 \
    --set-env-vars="$ENV_VARS" --quiet

URL=""
for attempt in 1 2 3 4 5 6; do
    URL="$(gcloud run services describe "$SERVICE_NAME" --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' --quiet 2>/dev/null || true)"
    [[ -n "$URL" ]] && break
    echo "    ...deployed, describe not serving yet — retry ${attempt}/6 in 10s"; sleep 10
done
[[ -n "$URL" ]] || { echo "ERROR: deployed but URL unreadable — rerun (idempotent)." >&2; exit 1; }

echo ""
echo "=================================================================="
echo "Deployed!  Console URL: $URL"
echo "Open it in a browser — that's the Race Control console."
echo "=================================================================="
