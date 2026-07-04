#!/usr/bin/env bash
# Step 3/5 — Deploy the State Writer (Cloud Run Worker Pool, Pub/Sub pull -> Firestore)  (~4 min). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 3/5 — Deploy the State Writer (Worker Pool)"
bash deploy/deploy_state_writer.sh
