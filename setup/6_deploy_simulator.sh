#!/usr/bin/env bash
# Step 6/8 — Deploy the telemetry simulator (Cloud Run)  (~4 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 6/8 — Deploy the telemetry simulator (Cloud Run)  (~4 min)"
( cd simulator && bash deploy.sh )
