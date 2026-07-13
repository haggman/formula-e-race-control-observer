#!/usr/bin/env bash
# Step 1/8 — Enable Google Cloud APIs  (~1 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 1/8 — Enable Google Cloud APIs  (~1 min)"
bash deploy/enable_apis.sh
