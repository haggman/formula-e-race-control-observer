#!/usr/bin/env bash
# Deploy the Telemetry Observer as a Cloud Run WORKER POOL.
#
# It PULLs fe-telemetry, runs the deterministic detector (stopped/prolonged/
# recovered/yaw), and PUBLISHes Observations to fe-observations for the correlator.
# It also writes a heartbeat to Firestore agent_status/telemetry.
#
# Why a worker pool: a Pub/Sub-pull consumer has no request surface (same shape as
# the state writer). NOTE: worker pools scale MANUALLY via --instances=N, NOT the
# --min-instances/--max-instances flags a regular Cloud Run *service* takes.
#
# The observer creates + seeks its OWN pull subscription at startup (live frames
# only), so we just ensure the topics exist and grant pubsub.editor.
#
# Required: PROJECT_ID and REGION (source activate.sh), or a gcloud project set.

set -euo pipefail

POOL_NAME="${POOL_NAME:-fe-telemetry-observer}"
TELEMETRY_TOPIC="${TELEMETRY_TOPIC:-fe-telemetry}"
OBSERVATIONS_TOPIC="${OBSERVATIONS_TOPIC:-fe-observations}"
SA_NAME="${SA_NAME:-fe-telemetry-observer-sa}"
REGION="${REGION:-us-central1}"
REPO_NAME="${REPO_NAME:-fe-services}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Pool:    $POOL_NAME   (worker pool, Pub/Sub pull)"
echo "In:      $TELEMETRY_TOPIC     Out: $OBSERVATIONS_TOPIC     SA: $SA_EMAIL"
echo "=================================================================="

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
        --display-name="Formula E Telemetry Observer" --project="$PROJECT_ID"

echo ">>> Granting roles (Pub/Sub pull+publish+seek, Firestore heartbeat; retry for propagation)..."
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

echo ">>> Ensuring Pub/Sub topics (observer self-creates + seeks its own subscription)..."
for topic in "$TELEMETRY_TOPIC" "$OBSERVATIONS_TOPIC"; do
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

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/telemetry-observer:$(date -u +%Y%m%d-%H%M%S)"
CB_CONFIG="$(mktemp)"
cat > "$CB_CONFIG" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '${IMAGE}', '-f', 'observers/Dockerfile', '.']
images: ['${IMAGE}']
EOF
gcloud builds submit "$REPO_ROOT" --config="$CB_CONFIG" --project="$PROJECT_ID"
rm -f "$CB_CONFIG"

echo ">>> Deploying Cloud Run worker pool (manual --instances scaling)..."
gcloud run worker-pools deploy "$POOL_NAME" \
    --image="$IMAGE" --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" --cpu=1 --memory=512Mi --instances=1 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
    --quiet

echo ""
echo "=================================================================="
echo "Deployed worker pool: $POOL_NAME"
echo "It pulls $TELEMETRY_TOPIC and publishes to $OBSERVATIONS_TOPIC."
echo "Logs:   gcloud run worker-pools logs read $POOL_NAME --region $REGION"
echo "=================================================================="
