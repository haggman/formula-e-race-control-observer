"""Green-light checks for the deployed application tier. Invoked by deploy/verify_app.sh.

The strongest liveness signal is each agent's Firestore HEARTBEAT (agent_status/*):
it only appears when the process is actually running AND can reach Firestore, so a
fresh heartbeat proves far more than "the Cloud Run resource exists". Same spirit
as the data-layer's 'is it advancing?' check.

The DEPLOYED tier is two agents (the correlator is NOT deployed — it runs locally,
in a Cloud Shell tab, because it holds the file the student edits):
  1. Telemetry Observer running — agent_status/telemetry fresh and not offline.
  2. Console serving — fe-console has a URL and it answers HTTP 200.

The correlator + video verifier are reported as an INFORMATIONAL line only: if a
heartbeat is present the student's local correlator is up; if not, that's expected
(they simply haven't started `python -m correlator.service` yet), so it never fails
the green light. Exit 0 iff the two DEPLOYED checks pass.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

REGION = os.environ["REGION"]
PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
CONSOLE_URL = os.environ.get("CONSOLE_URL", "").rstrip("/")
FRESH_S = int(os.environ.get("FE_HEARTBEAT_FRESH_S", "30"))   # heartbeat interval is 5s

OK, BAD, INFO = "  \033[92m✓\033[0m", "  \033[91m✗\033[0m", "  \033[96mℹ\033[0m"


def _fresh(doc: dict | None) -> bool:
    if not doc:
        return False
    age = time.time() - doc.get("updated_at_unix", 0)
    return age <= FRESH_S and doc.get("state") not in (None, "offline")


def _get_fresh(name: str, tries: int = 6, gap: float = 5.0) -> dict | None:
    """Poll agent_status/{name} until it's fresh (cold-start tolerant), else return
    the last-seen doc (possibly None) after ~tries*gap seconds."""
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT_ID)
    doc = None
    for i in range(tries):
        snap = db.collection("agent_status").document(name).get()
        doc = snap.to_dict() if snap.exists else None
        if _fresh(doc):
            return doc
        if i < tries - 1:
            time.sleep(gap)
    return doc


def _worker_pool_exists(name: str) -> bool:
    r = subprocess.run(
        ["gcloud", "run", "worker-pools", "describe", name,
         "--region", REGION, "--project", PROJECT_ID, "--format=value(name)"],
        capture_output=True, text=True)
    return r.returncode == 0 and name in (r.stdout or "")


def _age(doc: dict) -> int:
    return int(time.time() - doc.get("updated_at_unix", 0))


def check_telemetry() -> bool:
    hb = _get_fresh("telemetry")
    if _fresh(hb):
        print(f"{OK} Telemetry Observer online (state={hb.get('state')}, {_age(hb)}s ago)")
        return True
    print(f"{BAD} Telemetry Observer heartbeat stale/missing.")
    if not _worker_pool_exists("fe-telemetry-observer"):
        print("      Fix: bash setup/7_deploy_telemetry_observer.sh")
    else:
        print("      Deployed but not heartbeating — check the logs:")
        print("           gcloud run worker-pools logs read fe-telemetry-observer --region $REGION")
    return False


def report_correlator() -> None:
    """INFORMATIONAL only — the correlator runs LOCALLY, so its absence is normal.

    This never fails the green light. It just tells the student whether their local
    correlator (and the video verifier it arms) is currently heartbeating."""
    corr = _get_fresh("correlator", tries=1)   # a single quick look — don't wait on a local process
    vid = _get_fresh("video", tries=1)
    if _fresh(corr):
        v = f", video={vid.get('state')}" if _fresh(vid) else ", video not armed (running --no-verify?)"
        print(f"{INFO} Correlator (local) is up (correlator={corr.get('state')} {_age(corr)}s{v})")
    else:
        print(f"{INFO} Correlator not running yet — that's expected; it runs LOCALLY.")
        print("      Start it in a Cloud Shell tab (after `source activate.sh`):")
        print("           python -m correlator.service --no-verify   # Task 0 (telemetry only)")
        print("           python -m correlator.service               # Task 3 (with your verifier)")


def check_console() -> bool:
    if not CONSOLE_URL:
        print(f"{BAD} Console: fe-console not deployed / URL not found.")
        print("      Fix: bash setup/8_deploy_console.sh")
        return False
    try:
        with urllib.request.urlopen(CONSOLE_URL, timeout=10) as r:
            code = r.getcode()
    except Exception as e:
        print(f"{BAD} Console: {CONSOLE_URL} unreachable ({e}).")
        print("      Fix: check the service — gcloud run services logs read fe-console --region $REGION")
        return False
    ok = code == 200
    print(f"{OK if ok else BAD} Console serving: {CONSOLE_URL} (HTTP {code})")
    return ok


def main() -> int:
    print("Checking the deployed application tier (correlator runs locally)...\n")
    results = [check_telemetry(), check_console()]   # the two DEPLOYED agents
    report_correlator()                              # informational — never fails
    print()
    if all(results):
        print("  \033[92mAll green — the deployed agents are live. Open the console URL above,\033[0m")
        print("  \033[92mthen start your correlator locally to fill the Race Control + Video columns.\033[0m")
        return 0
    print("  \033[91mSome checks failed — see the fixes above, then re-run "
          "bash deploy/verify_app.sh\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
