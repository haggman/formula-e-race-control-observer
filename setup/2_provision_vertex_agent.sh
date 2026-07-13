#!/usr/bin/env bash
# Step 2/8 — Provision the Vertex AI service agent + mosaics read  (~1 min; propagates in background)
# WHERE: Cloud Shell, repo root, after `source activate.sh`. Idempotent.
set -euo pipefail
cd "$(dirname "$0")/.."
source setup/_lib.sh
require_activation
banner "Step 2/8 — Provision the Vertex AI service agent + mosaics read  (~1 min; propagates in background)"
bash deploy/provision_vertex_agent.sh
