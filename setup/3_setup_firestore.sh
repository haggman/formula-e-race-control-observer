#!/usr/bin/env bash
# Step 3/8 — Provision Firestore (Native mode, "now" store)  (~1 min)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 3/8 — Provision Firestore (Native mode, "now" store)  (~1 min)"
bash deploy/setup_firestore.sh
