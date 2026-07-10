#!/usr/bin/env bash
# Deploy the Correlator as a Cloud Run WORKER POOL.
#
# It SUBSCRIBEs fe-observations, fuses telemetry into incidents, and on a stop runs
# the VIDEO VERIFIER (Vertex Gemini reading gs:// mosaic slices). It PUBLISHes
# fe-incidents (fused recommendations) + the verifier's video reads back onto
# fe-observations (for the console's Video feed), and writes incidents/ +
# agent_status/ to Firestore. It polls the simulator's /status for the race clock.
#
# Why a worker pool: a Pub/Sub-pull consumer has no request surface. NOTE: worker
# pools scale MANUALLY via --instances=N (not --min/--max-instances).
#
# Required: PROJECT_ID and REGION (source activate.sh), or a gcloud project set.

set -euo pipefail

POOL_NAME="${POOL_NAME:-fe-correlator}"
OBSERVATIONS_TOPIC="${OBSERVATIONS_TOPIC:-fe-observations}"
INCIDENTS_TOPIC="${INCIDENTS_TOPIC:-fe-incidents}"
SA_NAME="${SA_NAME:-fe-correlator-sa}"
REGION="${REGION:-us-central1}"
REPO_NAME="${REPO_NAME:-fe-services}"
SIMULATOR_SERVICE="${SIMULATOR_SERVICE:-fe-simulator}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MOSAICS_BUCKET="${MOSAICS_BUCKET:-${PROJECT_ID}-fe-mosaics}"

# Discover the simulator URL (for the race clock). Non-fatal if absent, but the
# verifier's "wait for the window to play" gating needs it, so warn loudly.
SIM_URL="${SIM_URL:-$(gcloud run services describe "$SIMULATOR_SERVICE" \
    --region="$REGION" --project="$PROJECT_ID" --format='value(status.url)' 2>/dev/null || true)}"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Pool:    $POOL_NAME   (worker pool, Pub/Sub pull + Vertex Gemini)"
echo "In:      $OBSERVATIONS_TOPIC    Out: $INCIDENTS_TOPIC (+ $OBSERVATIONS_TOPIC)"
echo "Mosaics: gs://${MOSAICS_BUCKET}/mosaics    Sim: ${SIM_URL:-(unset!)}"
echo "SA:      $SA_EMAIL"
echo "=================================================================="
[[ -n "$SIM_URL" ]] || echo "WARNING: ${SIMULATOR_SERVICE} URL not found — deploy the simulator first, or the verifier may fire before the window has played."

echo ">>> Enabling APIs..."
gcloud services enable run.googleapis.com pubsub.googleapis.com \
    firestore.googleapis.com aiplatform.googleapis.com storage.googleapis.com \
    cloudbuild.googleapis.com artifactregistry.googleapis.com --project="$PROJECT_ID"

echo ">>> Waiting for Cloud Run API to settle..."
for attempt in 1 2 3 4 5 6; do
    gcloud run services list --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 && break
    echo "    ...retry ${attempt}/6 in 10s"; sleep 10
done

echo ">>> Ensuring service account..."
gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Formula E Correlator" --project="$PROJECT_ID"

echo ">>> Granting roles (Pub/Sub, Firestore, Vertex, mosaics read; retry for propagation)..."
for role in roles/pubsub.editor roles/datastore.user roles/aiplatform.user roles/storage.objectViewer; do
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

# --- Vertex AI service agent: reads the gs:// mosaics ON GEMINI'S BEHALF -------
# When Gemini reads a gs:// video, the *Vertex AI service agent* fetches the file
# (not the correlator SA above), so IT needs mosaics read. On a fresh project this
# agent must first be provisioned — otherwise the verifier hits
# "400 FAILED_PRECONDITION: service agents are being provisioned".
echo ">>> Provisioning the Vertex AI service agent + granting it mosaics read..."
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
gcloud beta services identity create --service=aiplatform.googleapis.com \
    --project="$PROJECT_ID" >/dev/null 2>&1 || true
AIPLATFORM_AGENT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"
granted=0
for attempt in 1 2 3 4 5 6; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${AIPLATFORM_AGENT}" --role="roles/storage.objectViewer" \
        --condition=None --quiet >/dev/null 2>&1; then granted=1; break; fi
    echo "    ...Vertex agent still provisioning — retry ${attempt}/6 in 10s"; sleep 10
done
if [[ "$granted" == "1" ]]; then
    echo "    granted roles/storage.objectViewer to ${AIPLATFORM_AGENT}"
else
    echo "    WARNING: could not grant the Vertex agent storage read yet — it may still"
    echo "             be provisioning (can take a few minutes on a fresh project)."
    echo "             Re-run this script shortly; the verifier retries automatically."
fi

echo ">>> Ensuring Pub/Sub topics (correlator self-creates + seeks its own subscription)..."
for topic in "$OBSERVATIONS_TOPIC" "$INCIDENTS_TOPIC"; do
    gcloud pubsub topics describe "$topic" --project="$PROJECT_ID" >/dev/null 2>&1 \
        || gcloud pubsub topics create "$topic" --project="$PROJECT_ID"
done

echo ">>> Building image with Cloud Build..."
gcloud artifacts repositories describe "$REPO_NAME" --location="$REGION" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud artifacts repositories create "$REPO_NAME" --location="$REGION" \
        --repository-format=docker --description="Formula E services" --project="$PROJECT_ID"

CB_BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for role in roles/logging.logWriter roles/artifactregistry.writer roles/storage.admin; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${CB_BUILD_SA}" --role="$role" \
        --condition=None --quiet >/dev/null 2>&1 || true
done
sleep 5

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/correlator:$(date -u +%Y%m%d-%H%M%S)"
CB_CONFIG="$(mktemp)"
cat > "$CB_CONFIG" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '${IMAGE}', '-f', 'correlator/Dockerfile', '.']
images: ['${IMAGE}']
EOF
gcloud builds submit "$REPO_ROOT" --config="$CB_CONFIG" --project="$PROJECT_ID"
rm -f "$CB_CONFIG"

echo ">>> Deploying Cloud Run worker pool (manual --instances scaling)..."
ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_GENAI_USE_VERTEXAI=1,MOSAICS_BUCKET=${MOSAICS_BUCKET}"
[[ -n "$SIM_URL" ]] && ENV_VARS="${ENV_VARS},SIM_URL=${SIM_URL}"
gcloud run worker-pools deploy "$POOL_NAME" \
    --image="$IMAGE" --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" --cpu=1 --memory=1Gi --instances=1 \
    --set-env-vars="$ENV_VARS" \
    --quiet

echo ""
echo "=================================================================="
echo "Deployed worker pool: $POOL_NAME"
echo "It fuses $OBSERVATIONS_TOPIC, verifies stops on CCTV, and writes incidents/."
echo "Logs:   gcloud run worker-pools logs read $POOL_NAME --region $REGION"
echo "=================================================================="
