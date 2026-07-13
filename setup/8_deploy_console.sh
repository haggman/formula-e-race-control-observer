#!/usr/bin/env bash
# Step 8/8 — Deploy the Race Control Console (Cloud Run service)  (~4 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 8/8 — Deploy the Race Control Console (Cloud Run service)  (~4 min)"
bash deploy/deploy_console.sh
