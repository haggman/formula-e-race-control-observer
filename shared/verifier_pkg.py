"""Resolve which VideoVerifier package to load — the starter/solution seam.

The correlator loads the verifier THROUGH this module, never with a hardcoded
import, so the SAME correlator runs either the student's build or the reference
answer key depending on one environment variable:

    starter.video_verifier   = the student's build   (DEFAULT for local dev)
    solution.video_verifier  = the complete reference (the answer key / the demo)

Two defaults, on purpose:
  * The CODE default here is `solution.video_verifier`, so a deployed container
    (which never sources activate.sh) gets the working reference.
  * `activate.sh` exports the per-session choice — `starter.video_verifier` by
    default for local work; instructors/demos override with
        export VERIFIER_PACKAGE=solution.video_verifier

This module fails LOUDLY on a bad package name (it never silently falls back to a
different verifier) — a wrong VERIFIER_PACKAGE should be obvious, not mysterious.
"""
from __future__ import annotations

import importlib
import os

VERIFIER_PACKAGE = os.environ.get("VERIFIER_PACKAGE", "solution.video_verifier").strip()


def verifier_module():
    """Import and return the selected package's `verifier` module.

    Raises ModuleNotFoundError (with a clear hint) if VERIFIER_PACKAGE is wrong.
    """
    try:
        return importlib.import_module(VERIFIER_PACKAGE + ".verifier")
    except ModuleNotFoundError as e:
        raise ModuleNotFoundError(
            f"VERIFIER_PACKAGE={VERIFIER_PACKAGE!r} — cannot import "
            f"'{VERIFIER_PACKAGE}.verifier'. Expected 'starter.video_verifier' or "
            f"'solution.video_verifier'. Did you `source activate.sh` from the repo "
            f"root? ({e})"
        ) from e


def get_verifier_class():
    """Return the VideoVerifier class from the selected package."""
    return verifier_module().VideoVerifier


def verifier_window():
    """Return the selected verifier's (LEAD_S, TAIL_S) window constants.

    The correlator mirrors these so it can tell the operator which footage it is
    reviewing. Falls back to (10, 50) if the module can't be loaded yet.
    """
    try:
        m = verifier_module()
        return int(m.LEAD_S), int(m.TAIL_S)
    except Exception:
        return 10, 50
