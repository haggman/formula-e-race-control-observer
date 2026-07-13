#!/usr/bin/env bash
# Step 4/8 — Stage the camera mosaics into this project's bucket  (~1 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 4/8 — Stage the camera mosaics into this project's bucket  (~1 min)"
bash deploy/stage_mosaics.sh
