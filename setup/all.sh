#!/usr/bin/env bash
# setup/all.sh — ONE command. Stands up everything the hack needs EXCEPT the
# correlator (that one you run locally: `python -m correlator.service`).
# WHERE: Cloud Shell, repo root, after `source activate.sh`
# WHAT:  bash setup/all.sh
#
# Order matters (see docs/HACK_DESIGN.md §6):
#   * the Colab link is printed FIRST, so you can start the notebook immediately;
#   * the Vertex AI service agent is provisioned EARLY (step 2), so it has
#     propagated by the time you run a Gemini cell — otherwise the first gs:// read
#     hits "400 FAILED_PRECONDITION: service agents are being provisioned";
#   * the slow Cloud Run builds come last, while you're already working in the
#     notebook. The correlator is NOT deployed — it runs locally in a Cloud Shell tab.
#
# Idempotent: rerun after a failure and completed steps fast-forward.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation venv

# --- FIRST: your notebook link (before any slow work) ---------------------------
bash setup/print_colab_link.sh

T0=$SECONDS
# Fast, notebook-unblocking steps (1-4) run first; the slow Cloud Run deploys
# (5-8) run while you're already in the notebook. Correlator excluded on purpose.
STEPS=(1_enable_apis
       2_provision_vertex_agent
       3_setup_firestore
       4_stage_mosaics
       5_deploy_state_writer
       6_deploy_simulator
       7_deploy_telemetry_observer
       8_deploy_console)
for step in "${STEPS[@]}"; do
    s0=$SECONDS
    bash "setup/${step}.sh"
    echo ""
    echo ">>> ${step} completed in $(( SECONDS - s0 ))s"
done

# --- Green-light check: data layer + the deployed agents (NOT the correlator) ---
echo ""
bash setup/verify.sh || echo "    (data-layer verify reported issues — see the fixes above)"
bash deploy/verify_app.sh || echo "    (app-tier verify reported issues — see the fixes above)"

# Cache the simulator URL (+ mosaics bucket) to .env.local, which activate.sh
# auto-loads — so future shells and the local correlator pick up SIM_URL instantly.
SIM_URL="$(timeout 10 gcloud run services describe fe-simulator \
    --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)"
CONSOLE_URL="$(timeout 10 gcloud run services describe fe-console \
    --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)"
{
    [[ -n "$SIM_URL" ]] && echo "export SIM_URL=${SIM_URL}"
    echo "export MOSAICS_BUCKET=${MOSAICS_BUCKET:-${PROJECT_ID}-fe-mosaics}"
} > .env.local

echo ""
echo "=================================================================="
printf "  setup/all.sh complete in %dm %02ds.\n" $(( (SECONDS - T0) / 60 )) $(( (SECONDS - T0) % 60 ))
echo "  Console:   ${CONSOLE_URL:-(deployed — re-run verify if blank)}"
echo ""
echo "  The correlator runs LOCALLY (it holds the file you edit). In a Cloud"
echo "  Shell tab, after 'source activate.sh':"
echo "    python -m correlator.service --no-verify     # Task 0: telemetry only"
echo "    python -m correlator.service                 # Task 3: with YOUR verifier"
echo "  Then open the console and jump to the Günther stop (race-second 693)."
echo "  (SIM_URL cached in .env.local; picked up automatically next activate.)"
echo "=================================================================="
