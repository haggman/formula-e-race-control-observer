# Smoke Test — Challenge 3

A ~15-minute pass to confirm a fresh deployment is healthy before you rely on it (before
an event, after a repo change). Stop at the first failure and note which step. If
anything prompts for input during setup, that's a finding — setup should be hands-off.

## 1. Activate + stand up the stack

```bash
source activate.sh
bash setup/all.sh
```

**PASS:** the Colab link prints **first**; steps 1–8 run without prompting; the data-layer
verify ends `All green — the data layer is live.` and the app-tier verify ends
`All green — the deployed agents are live.`
**FAIL signals:** any step hangs waiting for input; `FAILED_PRECONDITION` that doesn't
clear on a re-run of `setup/2_provision_vertex_agent.sh`; a red ✗ in either verify.

## 2. The starter/solution seam

```bash
echo "$VERIFIER_PACKAGE"                                   # -> starter.video_verifier
python -c "import shared.verifier_pkg as v; print(v.get_verifier_class().__module__)"
```

**PASS:** the env var is `starter.video_verifier`; the class resolves to
`starter.video_verifier.verifier`. With `VERIFIER_PACKAGE=solution.video_verifier` it
resolves to the solution.
**FAIL:** an ImportError, or it resolves to the wrong package.

## 3. The stubs are stubbed (starter) and the reference works (solution)

```bash
python -m starter.video_verifier.verifier --at 693 --cars 7      # expect NotImplementedError (Task 2/3 unwritten)
VERIFIER_PACKAGE=solution.video_verifier \
  python -m solution.video_verifier.verifier --at 693 --cars 7   # expect: VERDICT: BLOCKED  cameras=['Cam05']
```

**PASS:** the starter raises `NotImplementedError` from `_prompt` (proof the student has
real work); the solution prints **BLOCKED** with a Cam05 camera and a description.
**FAIL:** the solution errors (check the Vertex service-agent grant), or returns `unseen`
(check the mosaics staged + the `--at`/offsets).

## 4. The fusion policy (corroboration is the escalator)

```bash
python - <<'PY'
from datetime import datetime, timezone, timedelta
from shared.models import CorrelatedIncident, Observation, Modality, SignalType
from correlator import fusion
G = datetime(2024,5,12,13,4,0,tzinfo=timezone.utc)
def o(sig,car,s): return Observation(modality=Modality.TELEMETRY,signal=sig,ts_utc=G+timedelta(seconds=s),car_number=car,confidence=0.97,severity_hint=80)
def inc(obs,verdict=None): return CorrelatedIncident(incident_id="t",ts_utc=obs[0].ts_utc,car_numbers=[7],observations=obs,severity=80,video_verdict=verdict)
base=[o(SignalType.STOPPED_CAR,7,693),o(SignalType.PROLONGED_STOP,7,711)]
print("no video   ->", fusion.recommend_flag(inc(base)).flag.value)             # double_yellow
print("blocked    ->", fusion.recommend_flag(inc(base,"blocked")).flag.value)   # safety_car
PY
```

**PASS:** `no video -> double_yellow`, `blocked -> safety_car`. A prolonged stop alone is
NOT a Safety Car — only corroboration escalates.
**FAIL:** `no video` returns `safety_car` (the §4 policy change didn't land).

## 5. Console + local correlator — first light

```bash
python -m correlator.service --no-verify &     # telemetry only
# open the console URL, then click the Günther jump button
```

**PASS:** the Race Control column shows **DOUBLE YELLOW** for Günther and the Video Agent
column is empty (verifier off). Click **#33 (pit — no flag)** → the board stays green,
"routine pit stop." Then stop it, run
`VERIFIER_PACKAGE=solution.video_verifier python -m correlator.service`, and click
**Günther** again → the Video Agent narrates and Race Control escalates to **SAFETY CAR ·
corroborated**.
**FAIL:** the Race Control or Video column stays blank while the logs claim a verdict —
that's a dead publisher; restart and check the `fe-incidents` / `fe-observations`
publisher lines in the correlator's startup logs.

## Result

All five green → the build is event-ready. Any red → fix it and re-run from that step.
