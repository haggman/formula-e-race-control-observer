#!/usr/bin/env bash
# Step 2/5 — Provision Firestore (Native mode, "now" store)  (~1 min). Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 2/5 — Provision Firestore"
bash deploy/setup_firestore.sh
