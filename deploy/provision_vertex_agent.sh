#!/usr/bin/env bash
# Provision the Vertex AI service agent and grant it read on the mosaics — EARLY.
#
# THE LANDMINE THIS DEFUSES:
# When Gemini reads a gs:// video, the file is fetched by the *Vertex AI service
# agent* (service-<PROJECT_NUMBER>@gcp-sa-aiplatform.iam.gserviceaccount.com), a
# DIFFERENT identity from your own service account. On a fresh project that agent
# must first be PROVISIONED, and provisioning takes SEVERAL MINUTES to propagate.
# Until it does, the very first gs:// read fails with:
#   400 FAILED_PRECONDITION: Service agents are being provisioned...
#
# So we kick this off at the FRONT of setup — long before a student reaches a
# Gemini cell in the notebook — and grant the agent storage read while the slower
# Cloud Run builds run. By the time anyone calls the verifier, it has propagated.
#
# Idempotent: safe to re-run. Required: PROJECT_ID (source activate.sh).
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
[[ -n "$PROJECT_ID" ]] || { echo "ERROR: PROJECT_ID required (source activate.sh)" >&2; exit 1; }

echo ">>> Ensuring the Vertex AI (aiplatform) API is enabled..."
gcloud services enable aiplatform.googleapis.com --project="$PROJECT_ID" >/dev/null 2>&1 || true

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
AIPLATFORM_AGENT="service-${PROJECT_NUMBER}@gcp-sa-aiplatform.iam.gserviceaccount.com"

echo ">>> Provisioning the Vertex AI service agent (${AIPLATFORM_AGENT})..."
# 'services identity create' triggers provisioning of the managed agent.
gcloud beta services identity create --service=aiplatform.googleapis.com \
    --project="$PROJECT_ID" >/dev/null 2>&1 || true

echo ">>> Granting it roles/storage.objectViewer so Gemini can read the mosaics..."
granted=0
for attempt in 1 2 3 4 5 6; do
    if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${AIPLATFORM_AGENT}" --role="roles/storage.objectViewer" \
        --condition=None --quiet >/dev/null 2>&1; then granted=1; break; fi
    echo "    ...Vertex agent not visible to IAM yet (still provisioning) — retry ${attempt}/6 in 10s"
    sleep 10
done

if [[ "$granted" == "1" ]]; then
    echo "    granted roles/storage.objectViewer to ${AIPLATFORM_AGENT}"
    echo ">>> Vertex AI service agent provisioned. (Full propagation can still take a"
    echo "    few minutes — this is why we run it FIRST; the verifier also retries"
    echo "    FAILED_PRECONDITION automatically.)"
else
    echo "    WARNING: could not grant the Vertex agent storage read yet — it is likely"
    echo "             still provisioning (can take a few minutes on a brand-new project)."
    echo "             This step is idempotent: it will succeed on a re-run, and the"
    echo "             verifier retries FAILED_PRECONDITION on its own. Continuing."
fi
