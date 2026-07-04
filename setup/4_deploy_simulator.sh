#!/usr/bin/env bash
# Step 4/5 — Deploy the telemetry simulator (Cloud Run -> Pub/Sub)  (~4 min). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 4/5 — Deploy the telemetry simulator"
( cd simulator && bash deploy.sh )
