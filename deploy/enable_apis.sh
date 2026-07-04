#!/usr/bin/env bash
# Enable every API the Race Control Observer stack needs. Idempotent.
# Lab step zero: everything else assumes these are on.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
echo "Enabling APIs on project: ${PROJECT_ID}"

# Core stack (strict — nothing works without these):
#   run/pubsub/firestore = the data plane; aiplatform = Gemini Live + reporter;
#   cloudbuild/artifactregistry = building the sim + worker-pool images;
#   storage = pulling mosaics/frames; the rest are the console/observability set.
gcloud services enable \
    run.googleapis.com \
    pubsub.googleapis.com \
    firestore.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    storage.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    cloudresourcemanager.googleapis.com \
    compute.googleapis.com \
    --project "${PROJECT_ID}"

echo ""
echo "Done. Enabled core services:"
gcloud services list --enabled --project "${PROJECT_ID}" \
    --filter="config.name:(run OR pubsub OR firestore OR aiplatform OR storage)" \
    --format="value(config.name)"
