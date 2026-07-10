#!/usr/bin/env bash
# Deploy the whole APPLICATION tier (the three agents), in order, on top of an
# already-provisioned data layer (run setup/ first: APIs, Firestore, state writer,
# simulator, mosaics). Idempotent — safe to re-run.
#
#   1. Telemetry Observer  (worker pool)  — fe-telemetry  → fe-observations
#   2. Correlator          (worker pool)  — fe-observations → fe-incidents + verifier
#   3. Race Control Console (service)      — both buses → the browser UI
#
# The simulator must already be deployed (steps 2/3 discover its URL for the race
# clock + control plane). Required: PROJECT_ID + REGION (source activate.sh).

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

echo "##################################################################"
echo "# Formula E — deploying the application tier (3 agents)"
echo "##################################################################"

echo; echo ">>> [1/3] Telemetry Observer ..."
bash "${HERE}/deploy_telemetry_observer.sh"

echo; echo ">>> [2/3] Correlator ..."
bash "${HERE}/deploy_correlator.sh"

echo; echo ">>> [3/3] Race Control Console ..."
bash "${HERE}/deploy_console.sh"

echo; echo ">>> Green-light check ..."
bash "${HERE}/verify_app.sh" || echo "    (verify reported issues — see the fixes above)"

echo
echo "##################################################################"
echo "# Application tier deployed. Open the Console URL printed above."
echo "##################################################################"
