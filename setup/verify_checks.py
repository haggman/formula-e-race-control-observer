"""Green-light checks for the deployed data layer. Invoked by setup/verify.sh.

Three checks, each pass/fail with a one-line fix on failure:
  1. Simulator is up and PUBLISHING (SIM_URL/status → publish_count climbing).
  2. State writer is landing "now" in Firestore AND it's ADVANCING (race_states/
     {race_id}.race_time_s increases across a short wait) — proves the whole
     sim → Pub/Sub → worker-pool → Firestore path.
  3. Mosaics are staged in the student bucket (6 mp4 + manifest).

Exit 0 only if all pass.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.request

REGION = os.environ["REGION"]
PROJECT_ID = os.environ["GOOGLE_CLOUD_PROJECT"]
RACE_ID = os.environ.get("RACE_ID", "berlin_2024_r10")
SIM_URL = os.environ.get("SIM_URL", "").rstrip("/")
MOSAICS_BUCKET = os.environ.get("MOSAICS_BUCKET", f"{PROJECT_ID}-fe-mosaics")

OK, BAD = "  \033[92m✓\033[0m", "  \033[91m✗\033[0m"


def _get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        import json
        return json.loads(r.read())


def check_simulator() -> bool:
    if not SIM_URL:
        print(f"{BAD} Simulator: SIM_URL not set / fe-simulator not deployed.")
        print("      Fix: bash setup/4_deploy_simulator.sh ; then source activate.sh")
        return False
    try:
        s1 = _get_json(f"{SIM_URL}/status")
        time.sleep(3)
        s2 = _get_json(f"{SIM_URL}/status")
    except Exception as e:
        print(f"{BAD} Simulator: {SIM_URL}/status unreachable ({e}).")
        return False
    climbing = s2.get("publish_count", 0) > s1.get("publish_count", 0)
    mark = OK if climbing else BAD
    print(f"{mark} Simulator publishing: count {s1.get('publish_count')} → "
          f"{s2.get('publish_count')} (race_time_s {s2.get('race_time_s')})")
    if not climbing:
        print("      Fix: check it isn't paused — curl -X POST $SIM_URL/resume")
    return climbing


def check_state_writer() -> bool:
    from google.cloud import firestore
    db = firestore.Client(project=PROJECT_ID)
    ref = db.collection("race_states").document(RACE_ID)
    d1 = ref.get()
    if not d1.exists:
        print(f"{BAD} Firestore: race_states/{RACE_ID} missing — worker not writing.")
        print("      Fix: bash setup/3_deploy_state_writer.sh (and ensure the sim is publishing)")
        return False
    t1 = d1.to_dict().get("race_time_s")
    time.sleep(4)
    t2 = ref.get().to_dict().get("race_time_s")
    advancing = t2 is not None and t1 is not None and t2 != t1
    mark = OK if advancing else BAD
    print(f"{mark} Firestore 'now' advancing: race_time_s {t1} → {t2}")
    if not advancing:
        print("      Fix: the doc exists but isn't updating — check the worker pool logs:")
        print("           gcloud run worker-pools logs read fe-state-writer --region $REGION")
    return advancing


def check_mosaics() -> bool:
    import subprocess
    dest = f"gs://{MOSAICS_BUCKET}/mosaics"
    out = subprocess.run(["gcloud", "storage", "ls", f"{dest}/"],
                         capture_output=True, text=True)
    n = out.stdout.count(".mp4")
    has_manifest = "manifest.json" in out.stdout
    ok = n >= 1 and has_manifest
    mark = OK if ok else BAD
    print(f"{mark} Mosaics staged: {n} mp4 + manifest={'yes' if has_manifest else 'no'} in {dest}")
    if not ok:
        print("      Fix: bash setup/5_stage_mosaics.sh")
    return ok


def main() -> int:
    print("Checking the deployed data layer...\n")
    results = [check_simulator(), check_state_writer(), check_mosaics()]
    print()
    if all(results):
        print("  \033[92mAll green — the data layer is live.\033[0m")
        return 0
    print("  \033[91mSome checks failed — see the fixes above, then re-run "
          "bash setup/verify.sh\033[0m")
    return 1


if __name__ == "__main__":
    sys.exit(main())
