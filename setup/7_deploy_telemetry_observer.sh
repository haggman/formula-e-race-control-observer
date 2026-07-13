#!/usr/bin/env bash
# Step 7/8 — Deploy the Telemetry Observer (Cloud Run worker pool)  (~4 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 7/8 — Deploy the Telemetry Observer (Cloud Run worker pool)  (~4 min)"
bash deploy/deploy_telemetry_observer.sh
