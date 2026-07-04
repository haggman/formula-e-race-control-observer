#!/usr/bin/env bash
# Deploy the State Writer as a Cloud Run WORKER POOL that PULLs from Pub/Sub and
# overwrites race_states/{race_id} in Firestore. Borrowed from the Ch2
# fan-concierge state writer.
#
# Why a worker pool: a Pub/Sub-pull consumer has no request surface, so it wants a
# long-running worker, not an HTTP service — and pull drops all the push-auth
# plumbing (OIDC SA + run.invoker + tokenCreator).
#
# NOTE: worker pools scale MANUALLY via --instances=N — NOT the
# --min-instances/--max-instances flags a regular Cloud Run *service* takes (that
# mismatch is a classic worker-pool deploy failure).
#
# Required: PROJECT_ID and REGION (source activate.sh), or a gcloud project set.

set -euo pipefail

POOL_NAME="${SERVICE_NAME:-fe-state-writer}"
TOPIC_NAME="${TOPIC_NAME:-fe-telemetry}"
SUBSCRIPTION_NAME="${SUBSCRIPTION_NAME:-fe-state-writer-sub}"
SA_NAME="${SA_NAME:-fe-state-writer-sa}"
REGION="${REGION:-us-central1}"

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "=================================================================="
echo "Project: $PROJECT_ID   Region: $REGION"
echo "Pool:    $POOL_NAME   (worker pool, Pub/Sub pull)"
echo "Topic:   $TOPIC_NAME   Sub: $SUBSCRIPTION_NAME (pull)   SA: $SA_EMAIL"
echo "=================================================================="

echo ">>> Enabling APIs..."
gcloud services enable run.googleapis.com pubsub.googleapis.com \
    firestore.googleapis.com cloudbuild.googleapis.com --project="$PROJECT_ID"

echo ">>> Waiting for Cloud Run API to settle..."
for attempt in 1 2 3 4 5 6; do
    gcloud run services list --region="$REGION" --project="$PROJECT_ID" --quiet >/dev/null 2>&1 && break
    echo "    ...retry ${attempt}/6 in 10s"; sleep 10
done

echo ">>> Ensuring service account..."
gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud iam service-accounts create "$SA_NAME" \
        --display-name="Formula E State Writer" --project="$PROJECT_ID"

echo ">>> Granting roles (Firestore write + Pub/Sub pull; retry for propagation)..."
for role in roles/datastore.user roles/pubsub.subscriber; do
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

echo ">>> Ensuring Pub/Sub topic + PULL subscription..."
gcloud pubsub topics describe "$TOPIC_NAME" --project="$PROJECT_ID" >/dev/null 2>&1 \
    || gcloud pubsub topics create "$TOPIC_NAME" --project="$PROJECT_ID"
if gcloud pubsub subscriptions describe "$SUBSCRIPTION_NAME" --project="$PROJECT_ID" >/dev/null 2>&1; then
    PUSH_EP="$(gcloud pubsub subscriptions describe "$SUBSCRIPTION_NAME" \
        --project="$PROJECT_ID" --format='value(pushConfig.pushEndpoint)' 2>/dev/null || true)"
    if [[ -n "$PUSH_EP" ]]; then
        gcloud pubsub subscriptions delete "$SUBSCRIPTION_NAME" --project="$PROJECT_ID" --quiet
        gcloud pubsub subscriptions create "$SUBSCRIPTION_NAME" --topic="$TOPIC_NAME" \
            --ack-deadline=60 --message-retention-duration=10m --project="$PROJECT_ID"
    else
        gcloud pubsub subscriptions update "$SUBSCRIPTION_NAME" --ack-deadline=60 --project="$PROJECT_ID"
    fi
else
    gcloud pubsub subscriptions create "$SUBSCRIPTION_NAME" --topic="$TOPIC_NAME" \
        --ack-deadline=60 --message-retention-duration=10m --project="$PROJECT_ID"
fi

echo ">>> Building image with Cloud Build..."
REPO_NAME="${REPO_NAME:-fe-services}"
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

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/state-writer:$(date -u +%Y%m%d-%H%M%S)"
CB_CONFIG="$(mktemp)"
cat > "$CB_CONFIG" <<EOF
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '${IMAGE}', '-f', 'state_writer/Dockerfile', '.']
images: ['${IMAGE}']
EOF
gcloud builds submit "$REPO_ROOT" --config="$CB_CONFIG" --project="$PROJECT_ID"
rm -f "$CB_CONFIG"

echo ">>> Deploying Cloud Run worker pool (manual --instances scaling)..."
gcloud run worker-pools deploy "$POOL_NAME" \
    --image="$IMAGE" --region="$REGION" --project="$PROJECT_ID" \
    --service-account="$SA_EMAIL" --cpu=1 --memory=512Mi --instances=1 \
    --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT_ID},SUBSCRIPTION_NAME=${SUBSCRIPTION_NAME}" \
    --quiet

echo ""
echo "=================================================================="
echo "Deployed worker pool: $POOL_NAME"
echo "It pulls $SUBSCRIPTION_NAME and overwrites race_states/ in Firestore."
echo "Logs:   gcloud run worker-pools logs read $POOL_NAME --region $REGION"
echo "=================================================================="
