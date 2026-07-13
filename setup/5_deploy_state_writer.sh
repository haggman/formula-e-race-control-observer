#!/usr/bin/env bash
# Step 5/8 — Deploy the State Writer (Cloud Run worker pool)  (~4 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 5/8 — Deploy the State Writer (Cloud Run worker pool)  (~4 min)"
bash deploy/deploy_state_writer.sh
