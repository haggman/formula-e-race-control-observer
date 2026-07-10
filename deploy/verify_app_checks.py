"""Green-light checks for the deployed application tier. Invoked by deploy/verify_app.sh.

The strongest liveness signal is each agent's Firestore HEARTBEAT (agent_status/*):
it only appears when the process is actually running AND can reach Firestore, so a
fresh heartbeat proves far more than "the Cloud Run resource exists". Same spirit
as the data-layer's 'is it advancing?' check.

Three checks, each pass/fail with a one-line fix on failure:
  1. Telemetry Observer running — agent_status/telemetry fresh and not offline.
  2. Correlator + video verifier running — agent_status/correlator AND
     agent_status/video both fresh.
  3. Console serving — fe-console has a URL and it answers HTTP 200.

Heartbeats are polled briefly (cold-started containers take a few seconds to write
their first one), so this is safe to run immediately after a deploy. Exit 0 only if
all pass.
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

OK, BAD = "  \033[92m✓\033[0m", "  \033[91m✗\033[0m"


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
        print("      Fix: bash deploy/deploy_telemetry_observer.sh")
    else:
        print("      Deployed but not heartbeating — check the logs:")
        print("           gcloud run worker-pools logs read fe-telemetry-observer --region $REGION")
    return False


def check_correlator() -> bool:
    corr = _get_fresh("correlator")
    vid = _get_fresh("video")
    corr_ok, vid_ok = _fresh(corr), _fresh(vid)
    if corr_ok and vid_ok:
        print(f"{OK} Correlator + video verifier online "
              f"(correlator={corr.get('state')} {_age(corr)}s, video={vid.get('state')} {_age(vid)}s)")
        return True
    print(f"{BAD} Correlator heartbeat: correlator="
          f"{'fresh' if corr_ok else 'stale/missing'}, video={'fresh' if vid_ok else 'stale/missing'}")
    if not _worker_pool_exists("fe-correlator"):
        print("      Fix: bash deploy/deploy_correlator.sh")
    else:
        print("      Deployed but not (fully) heartbeating — check the logs:")
        print("           gcloud run worker-pools logs read fe-correlator --region $REGION")
    return False


def check_console() -> bool:
    if not CONSOLE_URL:
        print(f"{BAD} Console: fe-console not deployed / URL not found.")
        print("      Fix: bash deploy/deploy_console.sh")
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
    print("Checking the deployed application tier...\n")
    results = [check_telemetry(), check_correlator(), check_console()]
    print()
    if all(results):
        print("  \033[92mAll green — the agents are live. Open the console URL above.\033[0m")
        return 0
    print("  \033[91mSome checks failed — see the fixes above, then re-run "
          "bash deploy/verify_app.sh\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
