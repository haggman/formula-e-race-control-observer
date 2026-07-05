#!/usr/bin/env bash
# Activate the formula-e-race-control-observer dev environment.
#
# Usage (must use 'source', not bash):
#     source activate.sh
#
# Idempotent: safe to source multiple times per shell session.

# --- Region (matches the gs://class-demo bucket region) ---
export REGION="${REGION:-us-central1}"

# --- Project ID (Qwiklabs sets this in gcloud config) ---
export PROJECT_ID="${PROJECT_ID:-$(gcloud config get-value project 2>/dev/null)}"
if [[ -z "$PROJECT_ID" ]]; then
    echo "ERROR: no project set. Run 'gcloud config set project YOUR_PROJECT'." >&2
    return 1 2>/dev/null || exit 1
fi

# --- Virtual environment ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

# Load cached dynamic vars (SIM_URL etc.) that setup/all.sh discovered, so
# activation is instant and needs no gcloud round-trip.
# shellcheck disable=SC1091
[[ -f "${REPO_ROOT}/.env.local" ]] && source "${REPO_ROOT}/.env.local"

if [[ ! -d "$VENV_DIR" ]]; then
    echo ">>> Creating virtual environment at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# --- Install / update requirements (stamp so we don't pip on every source) ---
REQ_FILE="${REPO_ROOT}/requirements.txt"
STAMP_FILE="${VENV_DIR}/.req-stamp"
if [[ -f "$REQ_FILE" ]]; then
    if [[ ! -f "$STAMP_FILE" ]] || [[ "$REQ_FILE" -nt "$STAMP_FILE" ]]; then
        echo ">>> Installing requirements..."
        pip install --upgrade pip wheel setuptools >/dev/null 2>&1 || true
        pip install -r "$REQ_FILE" 2>&1 | grep -E "^(Collecting|Successfully|ERROR)" || true
        pip install -e "$REPO_ROOT" >/dev/null 2>&1 || true
        touch "$STAMP_FILE"
    fi
fi

# --- Vertex AI / Gemini (the Live video observer + the reporter agent) ---
export GOOGLE_GENAI_USE_VERTEXAI=1
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

# --- ADC preflight — Python clients (Firestore/Vertex) use ADC, not gcloud
# login. Stale ADC makes Firestore reads HANG silently. Catch it here. ---
if ! timeout 8 gcloud auth application-default print-access-token >/dev/null 2>&1; then
    echo ""
    echo "  ⚠️  Application Default Credentials look stale or unavailable."
    echo "      Python clients (Firestore/Vertex) will HANG on reads until fixed."
    echo "      Run:  gcloud auth application-default login   then re-source."
    echo ""
fi

# --- Discover the simulator URL (no-op until it's deployed) ---
if [[ -z "${SIM_URL:-}" ]]; then
    SIM_URL="$(timeout 10 gcloud run services describe fe-simulator \
        --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)"
fi
export SIM_URL

# --- Mosaics bucket (the student's copy of the video plane) ---
export MOSAICS_BUCKET="${MOSAICS_BUCKET:-${PROJECT_ID}-fe-mosaics}"

# --- Race + demo constants ---
export RACE_ID="${RACE_ID:-berlin_2024_r10}"

# --- Cache the dynamic values so future shells pick them up instantly ---
{
    [[ -n "${SIM_URL:-}" ]] && echo "export SIM_URL=${SIM_URL}"
    echo "export MOSAICS_BUCKET=${MOSAICS_BUCKET}"
} > "${REPO_ROOT}/.env.local" 2>/dev/null || true

echo ""
echo "=================================================================="
echo "  formula-e-race-control-observer activated"
echo "=================================================================="
echo "  Project:   $PROJECT_ID"
echo "  Region:    $REGION"
echo "  Venv:      $VENV_DIR"
echo "  Simulator: ${SIM_URL:-(none — run setup/all.sh)}"
echo "  Mosaics:   gs://${MOSAICS_BUCKET}"
echo "=================================================================="
