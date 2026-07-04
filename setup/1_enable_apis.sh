#!/usr/bin/env bash
# Step 1/5 — Enable Google Cloud APIs  (~1 min). Idempotent.
# WHERE: Cloud Shell, repo root, after `source activate.sh`
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 1/5 — Enable Google Cloud APIs"
bash deploy/enable_apis.sh
