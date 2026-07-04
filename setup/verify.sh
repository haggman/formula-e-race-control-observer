#!/usr/bin/env bash
# Green-light check — verifies the deployed data layer.  (~1 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`
# WHAT:  bash setup/verify.sh
# Run any time; setup/all.sh runs it automatically as its last step.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation venv
banner "Verify — green-light check"

# Discover the simulator URL if not already exported. Bounded + non-interactive
# (</dev/null + timeout) so a project where setup hasn't run can't hang here.
export SIM_URL="${SIM_URL:-$(timeout 10 gcloud run services describe fe-simulator \
    --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)}"

python setup/verify_checks.py
