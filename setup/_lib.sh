# Shared helpers for the setup/ scripts. Source this, don't execute it.

# Every setup script needs PROJECT_ID + REGION (exported by activate.sh).
# Scripts that run Python additionally need the venv: require_activation venv
require_activation() {
    local need_venv="${1:-}"
    if [[ -z "${PROJECT_ID:-}" || -z "${REGION:-}" ]]; then
        echo "ERROR: PROJECT_ID / REGION not set." >&2
        echo "  Fix: from the repo root run:  source activate.sh" >&2
        exit 1
    fi
    if [[ "$need_venv" == "venv" && -z "${VIRTUAL_ENV:-}" ]]; then
        echo "ERROR: the project venv is not active." >&2
        echo "  Fix: from the repo root run:  source activate.sh" >&2
        exit 1
    fi
}

banner() {
    echo ""
    echo "=================================================================="
    echo "  $1"
    echo "=================================================================="
}
