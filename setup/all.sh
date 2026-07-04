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

echo ""
echo "=================================================================="
printf "  setup/all.sh complete in %dm %02ds\n" $(( (SECONDS - T0) / 60 )) $(( (SECONDS - T0) % 60 ))
echo "  Next: source activate.sh   (picks up SIM_URL in this shell)"
echo "=================================================================="
