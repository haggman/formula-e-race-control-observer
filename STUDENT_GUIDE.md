<!-- TINYURL: https://tinyurl.com/FE-Hack-3  (set this to the short link you hand out) -->
# Build a Formula E Video Verifier

You are going to give Race Control a second sense. The telemetry can see a car stop —
but it *cannot* tell a retirement in a blind corner from a routine pit stop. You'll
build the piece that looks at the CCTV and answers the one question that resolves it:
**is the racing line actually blocked?** When your code works, it's what authorises the
Safety Car.

## STEP 0 — do this FIRST, before the instructor starts talking

In Cloud Shell, from the repo root:

```bash
source activate.sh        # venv + project + the starter/solution seam
bash setup/all.sh         # prints your Colab link FIRST, then builds the stack (~15 min)
```

The first thing it prints is a **Colab link — click it in Cloud Shell** (not from these
notes; see "When things go sideways"). That opens your notebook. Start there while the
rest builds. Then:

⏸ **STOP HERE. Eyes up front.** The instructor opens with the finished thing so you
know what you're building toward.

## Welcome back

Here's the whole design in one paragraph. A Formula E race replays at 1 Hz. A
**Telemetry Observer** (given) watches the cars' own data and flags a car that has
stopped — cheap, deterministic, instant. A **Correlator** (given) fuses observations,
decides a flag, and drafts a report for a human to approve with one click. The missing
piece is the **Video Verifier** — *you build it* — which reads the trackside CCTV and
confirms whether a flagged stop is a real, persistent blockage. Until it works, Race
Control can only offer *"double yellow — pending video confirmation."* When it works,
the recommendation escalates to **Safety Car · corroborated**.

## The map

Three browser worlds, one Cloud Shell:

- **The console** (a Cloud Run URL) — the Race Control pit wall. You didn't write it.
- **The notebook** (`fe_video_lab.ipynb` in Colab) — your workbench. Fast loop.
- **Cloud Shell** — where you run your correlator: `python -m correlator.service`.

You edit exactly one file: `starter/video_verifier/verifier.py`. Stuck? The same file
in `solution/` is the complete answer key — opening it is **shipping, not cheating**.

## Two minutes of Formula E

A Safety Car neutralises the race when the track is unsafe — a stranded car, debris.
Call one when you shouldn't and you've frozen the racing for nothing; *fail* to call one
and you leave a stopped car in a 200 km/h blind corner. The cost of a wrong call runs
both ways, which is exactly why Race Control wants two senses agreeing before it acts.

## The two senses (the architecture in one idea)

> **A car stopped in the pit lane and a car stopped in the middle of Turn 3 look
> identical in telemetry.** Speed is zero in both.

Telemetry alone can't tell them apart with confidence. So it raises its hand —
*"car #7 has been stationary for 18 seconds"* — and the cameras answer the question it
can't: **is the racing line actually blocked?** Two senses, because one is ambiguous.
And the flag itself is decided by **deterministic code** (`correlator/fusion.py`), never
by the model — a safety call must be explainable and repeatable. The model narrates; the
policy decides.

Read [`HOW_IT_WORKS.md`](HOW_IT_WORKS.md) before you edit code. It's ten minutes and it's
the difference between a verifier that works and one that confidently lies to Race Control.

---

# The build — four tasks, ~2h15

Each task follows the same scaffold: **Open** → **Your challenge** → **Test it** →
**What just happened (and why that's the point)** → **Done looks like** → **Checkpoint**.

## Task 0 — Orientation: see the missing sense (~15 min)

**Open:** the console (its URL is printed by setup). In a fresh Cloud Shell tab:

```bash
source activate.sh
python -m correlator.service --no-verify        # telemetry only — no video yet
```

**Your challenge:** in the console, jump to the **Günther** incident (race-second 693).
Read what Race Control can and cannot say.

**Test it:** you should see telemetry nail the stop — *car #7 stopped, still stopped
after 18s, confirmed blockage* — and the recommendation sitting at **DOUBLE YELLOW,
pending video confirmation**. The **Video Agent column is dead.** Now jump to the
**#33** incident (race-second 94): the system spots a stationary car and correctly says
*"routine pit stop, not a track incident"* — no flag. It's as proud of what it *doesn't*
flag as what it does.

**What just happened (and why that's the point):** the system has one sense and *knows*
it. It won't guess a Safety Car from telemetry alone, because a stationary car is
ambiguous. That gap — the dead video column — is the thing you're about to fill.

**Done looks like:** Günther = double yellow; #33 = no flag with a visible reason.

**Checkpoint:** you can state, out loud, why a prolonged stop is *still* only a double
yellow here.

## Task 1 — First Gemini call (~20 min, in the notebook)

**Open:** `fe_video_lab.ipynb`. Run sections 0–4.

**Your challenge:** get one camera group's window into Gemini and have it *describe* the
scene around the stop. You're pointing Gemini straight at a `gs://` mosaic and passing
`videoMetadata` start/end offsets, so it decodes **only** the 60-second window — no
download, no ffmpeg.

**Test it:** section 4 returns raw text — Gemini telling you what it sees at race-second
693. That's the magic moment: a model reading a slice of a video in a bucket.

**What just happened (why):** you proved the alignment first (section 3 — mp4 offset *N*
== race-second *N*), so "the footage around the stop" needs *no* clock conversion.
**Everything you build rests on that fact.**

**Done looks like:** a paragraph of description comes back for the Günther window.

**Checkpoint:** you can explain why 24 cameras became 6 Gemini calls.

## Task 2 — The persistence prompt (~40 min, in the notebook) — THE HEART

Spend your time here. This is the best discussion of the day.

**Open:** section 5 of the notebook — the structured sweep. It's your prompt workbench.

**Your challenge:** write the question so that **one** answer separates a retirement from
a spin-and-recover. The naive prompt — *"is there a stopped car?"* — cannot: a car can be
stopped at second 5 and gone by second 50. Both are "a stopped car"; one needs a Safety
Car and the other needs nothing. The question that works is about the **track's state at
the END of the window**, not a car's identity at a moment:

> *"By the END of this window, is the racing line still BLOCKED, or did it CLEAR?"*

Your prompt must return exactly this JSON (the rest of the code depends on it):
`blockage`, `cleared`, `panel` (`TL|TR|BL|BR|none`), `feed_live`, `seen_car`,
`what_you_see`, `confidence`.

**Test it:** run the three cases in section 5. You want **blocked / cleared / blocked**
for 693 (Günther, retires), 1373 (Evans, big moment but drives away), 1780 (Mortara).
If Evans comes back `blocked`, your question is asking about a moment, not the end.

**What just happened (why):** the 50-second *tail* is the entire design. It's what lets
one question do all the work. Ask at the end of the window and a retirement stays
blocked while a recovery clears itself. *(War story: verify too early — before the
window has played — and you're asking about footage that hasn't happened. The model will
oblige you with a hallucinated crash.)*

**Done looks like:** blocked / cleared / blocked, reliably, across a couple of reruns.

**Checkpoint:** show your prompt and defend the one sentence that makes it work.

## Task 3 — Port it: the board lights up (~40 min)

**Open:** `starter/video_verifier/verifier.py`. Three methods are stubbed:

- `_prompt(...)` — the question + JSON contract you just tuned.
- `VideoVerifier._verify_group(...)` — one Gemini call over one `gs://` slice → parsed
  dict (the file docstring names the exact API surface: `types.Part(file_data=…,
  video_metadata=…)`).
- `VideoVerifier._aggregate(...)` — fuse the six replies into one `VideoVerdict`. Honour
  all four states, and keep **`unseen`** (ran, saw nothing) distinct from **`error`**
  (never ran). An outage must not masquerade as an all-clear.

**Your challenge:** fill the three stubs so the acceptance tests pass, then let the
correlator use it.

**Test it — standalone first (fast):**

```bash
python -m starter.video_verifier.verifier --at 693 --cars 7        # -> blocked, Cam05  (Günther)
python -m starter.video_verifier.verifier --at 1698 --cars 17 23   # -> blocked, Cam07  (Fenestraz + Nato)
python -m starter.video_verifier.verifier --at 1780 --cars 48      # -> blocked, Cam07  (Mortara)
```

**Then the real test:** restart your correlator **with the verifier armed**, jump to
Günther, and watch it escalate:

```bash
python -m correlator.service        # no --no-verify this time
```

The Video Agent column narrates — *[QUEUED] → [ANALYZING] → CONFIRMED, track blocked,
Cam05* — and the recommendation goes from **DOUBLE YELLOW** to **SAFETY CAR ·
corroborated**. The Approve button lights up.

**What just happened (why):** your verdict is the *only* thing that lifts a stop to a
Safety Car (see the fusion policy — corroboration is the sole escalator). The board
lighting up is *your* code authorising the flag. Edit → Ctrl-C → rerun: the deployed
console updates in seconds, because the correlator runs locally.

**Done looks like:** Günther, Fenestraz+Nato, and Mortara all reach **Safety Car ·
corroborated**; #33 stays a note.

**Checkpoint demo:** jump to Günther live and narrate the escalation to the room.

---

## Acceptance tests (your green light)

```bash
python -m starter.video_verifier.verifier --at 693 --cars 7        # blocked, Cam05  (Günther #7 retires)
python -m starter.video_verifier.verifier --at 1698 --cars 17 23   # blocked, Cam07  (Fenestraz #23 + Nato #17)
python -m starter.video_verifier.verifier --at 1780 --cars 48      # blocked, Cam07  (Mortara #48)
```

A stop that recovers should come back `cleared`, not `blocked` — that's the false-alarm
veto. The camera ids are what the reference mapping returns; yours should agree.

## Question bank (for your demos)

| Ask | What it proves |
|---|---|
| Jump to #33 (pit) — why no flag? | The pit-lane guard: a stationary car isn't automatically a blockage. |
| Jump to Günther with `--no-verify`, then with the verifier | Corroboration is the escalator — one sense is only a double yellow. |
| Verify at race-second 693 vs at 700-ish before the tail plays | "Confirm from what happened; never peek ahead" — the tail is the design. |
| Fenestraz + Nato: why does the flag stand after Nato drives off? | The flag holds while *#23* is stranded; #23 recovering is what would clear it. |
| Kill the verifier mid-run (or point it at a bad bucket) | `error` ≠ `unseen`: an outage must not read as an all-clear. |

## When things go sideways

| Symptom | Cause | Fix |
|---|---|---|
| Everything's broken / commands "not found" | forgot to activate | `source activate.sh` from the repo root — always the first fix |
| Colab link opened the wrong project | clicked from these notes, not Cloud Shell | rerun `bash setup/print_colab_link.sh` and click it *in Cloud Shell* |
| `400 FAILED_PRECONDITION: service agents are being provisioned` | fresh-project Vertex service agent still propagating | wait a few minutes (setup provisions it early; the verifier retries) — don't change your code |
| `403 / permission denied` reading the mosaics | Vertex service agent lacks bucket read | rerun `bash setup/2_provision_vertex_agent.sh` |
| Gemini "hallucinated a crash" on a car doing 130 km/h | verified too early, or verified a non-stop | only verify a real *stop*, and after the window has played (Task 2) |
| It's really slow (~60s per stop) | the sweep is sequential on purpose | that's **Bonus 1** — make it concurrent |
| Flag never reaches Safety Car | verdict isn't returning `blocked`, or you didn't restart the correlator after editing | check the standalone CLI, then Ctrl-C + rerun `python -m correlator.service` |
| "It says no CCTV view" | wrong `videoMetadata` offsets or group list | check your `_verify_group` offsets and that all six groups ran |

## Finished early?

The board's lit — now make it better. See [`BONUS.md`](BONUS.md): make the sweep
concurrent (feel ~60s become ~10s), teach it to name the car by livery, and the best
design argument in the hack — *should a camera-blind Safety Car be paralysed, or escalate
anyway?*
