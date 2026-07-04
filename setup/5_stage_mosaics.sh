#!/usr/bin/env bash
# Step 5/5 — Stage the camera mosaics into this project's bucket  (~1 min). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 5/5 — Stage the camera mosaics"
bash deploy/stage_mosaics.sh
