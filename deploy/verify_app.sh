#!/usr/bin/env bash
# Green-light check — verifies the deployed APPLICATION tier (the 3 agents). (~30s)
# WHERE: Cloud Shell, repo root, after `source activate.sh`
# WHAT:  bash deploy/verify_app.sh
# Run any time; deploy/deploy_app.sh runs it automatically as its last step.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation venv
banner "Verify — application tier (3 agents)"

# Discover the console URL if not already exported. Bounded + non-interactive
# (</dev/null + timeout) so a project where the app tier hasn't deployed can't hang.
export CONSOLE_URL="${CONSOLE_URL:-$(timeout 10 gcloud run services describe fe-console \
    --region "$REGION" --format='value(status.url)' </dev/null 2>/dev/null || true)}"

python deploy/verify_app_checks.py
