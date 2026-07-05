#!/usr/bin/env bash
# setup/all.sh — one-command data-layer install: steps 1-5, then verify.
# WHERE: Cloud Shell, repo root, after `source activate.sh`
# WHAT:  bash setup/all.sh
# Budget ~12-15 min on a fresh project. Idempotent: rerun after a failure and
# completed steps fast-forward.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation venv

T0=$SECONDS
STEPS=(1_enable_apis 2_setup_firestore 3_deploy_state_writer
       4_deploy_simulator 5_stage_mosaics)
for step in "${STEPS[@]}"; do
    s0=$SECONDS
    bash "setup/${step}.sh"
    echo ""
    echo ">>> ${step} completed in $(( SECONDS - s0 ))s"
done

bash setup/verify.sh

# Discover the now-deployed simulator URL and cache it (+ mosaics bucket) to
# .env.local, which activate.sh auto-loads. This is why no re-source is needed:
# scripts already re-discover SIM_URL, and future shells read it from here.
SIM_URL="$(timeout 10 gcloud run services describe fe-simulator \
    --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)"
{
    [[ -n "$SIM_URL" ]] && echo "export SIM_URL=${SIM_URL}"
    echo "export MOSAICS_BUCKET=${MOSAICS_BUCKET:-${PROJECT_ID}-fe-mosaics}"
} > .env.local

echo ""
echo "=================================================================="
printf "  setup/all.sh complete in %dm %02ds — data layer is live.\n" \
    $(( (SECONDS - T0) / 60 )) $(( (SECONDS - T0) % 60 ))
echo "  Simulator:  ${SIM_URL:-(deployed — re-run verify if blank)}"
echo "  Jump to the hero incident (13:32 corroborated stop):"
echo "    curl -X POST ${SIM_URL}/jump -H 'content-type: application/json' -d '{\"race_time_s\": 1680}'"
echo "  (SIM_URL cached in .env.local; picked up automatically next activate.)"
echo "=================================================================="
