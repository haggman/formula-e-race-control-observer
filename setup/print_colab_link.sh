#!/usr/bin/env bash
# Print the one-click Colab Enterprise import link for the student notebook.
# WHERE: Cloud Shell (repo root). PRINT IT FROM HERE, not from the written guide:
# Cloud Shell runs INSIDE the Cloud console window, so a link clicked here opens in
# the SAME browser session + project. A link clicked from the instructions (a normal
# Chrome window, often incognito) lands in the wrong session. Terminal-print avoids
# that trap entirely. setup/all.sh prints this first, before any slow work.
set -euo pipefail
echo ""
echo "=================================================================="
echo "  📓  OPEN YOUR NOTEBOOK — click this link in THIS Cloud Shell:"
echo "=================================================================="
echo ""
echo "  https://console.cloud.google.com/agent-platform/colab/import/https:%2F%2Fraw.githubusercontent.com%2Fhaggman%2Fformula-e-race-control-observer%2Fmain%2Fnotebooks%2Ffe_video_lab.ipynb"
echo ""
echo "  (Colab Enterprise → it imports notebooks/fe_video_lab.ipynb. Start there"
echo "   while the rest of the stack builds — the notebook is real work, not filler.)"
echo "=================================================================="
