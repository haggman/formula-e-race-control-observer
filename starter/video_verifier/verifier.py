"""VideoVerifier — *** THIS IS THE FILE YOU BUILD. ***

Two things are stubbed. Everything else here is given and working, and the plumbing
you don't need lives next door in `_given.py`.

    _prompt(...)                 the question you wrote in the notebook
    VideoVerifier._aggregate     fuse six replies into ONE verdict

The correlator already calls verify() at the right moment with the right arguments.

Test it standalone (after `source activate.sh`), no full stack required:
    python -m starter.video_verifier.verifier --at 693 --cars 7

Stuck? `solution/video_verifier/verifier.py` is the same file, finished. Opening it is
shipping, not cheating.

Your verdict feeds the correlator's fusion:
    blocked  -> corroborated -> SAFETY_CAR
    cleared  -> veto         -> no Safety Car (the car recovered)
    unseen   -> telemetry-only (no camera saw it)
    error    -> the check couldn't RUN (never an all-clear)
"""
from __future__ import annotations

from ._given import (                       # plumbing — read it only if you want to
    LEAD_S, TAIL_S, LIVERIES, PANEL_POS,
    VideoVerdict, VerifierBase,
    _parse, _short_error, logger, run_cli,
)


# ---------------------------------------------------------------------------
# The question.  Paste in the _prompt you built and tuned in the notebook.
# ---------------------------------------------------------------------------
def _prompt(cams: list[str], t: int, start: int, end: int, cars=None) -> str:
    """Build the text prompt for ONE 2x2 mosaic clip.

    Same signature and same given `context`/`json_contract` tail as the notebook cell,
    so your notebook `_prompt` drops straight in — or just paste your `logic`.
    """
    tl, tr, bl, br = (cams + ["?", "?", "?", "?"])[:4]

    # ===== YOUR LOGIC (from the notebook) ==================================
    #   what makes blockage true vs cleared?
    #   which panel (TL/TR/BL/BR) is it in?  -> panel   (code maps panel -> camera)
    #   the car's number if legible, else null -> seen_car
    #   one-line description -> what_you_see ; other cars moving -> feed_live ; confidence
    #   (`cars` + the LIVERIES table are Bonus 2 — naming the car.)
    logic = """
    << paste the question you built in the notebook here >>
    """

    # ===== GIVEN — clip context + the JSON contract the code depends on ====
    context = (f"This is a ~{end - start}s CCTV clip — a 2x2 mosaic of four cameras: "
               f"TL={tl}, TR={tr}, BL={bl}, BR={br}.")
    json_contract = ('Respond with a SINGLE JSON object: {"blockage": bool, "cleared": bool, '
                     '"panel": "TL|TR|BL|BR|none", "feed_live": bool, '
                     '"seen_car": <car number if clearly readable, else null>, '
                     '"what_you_see": string, "confidence": number}')

    if "<<" in logic:
        raise NotImplementedError(
            "Paste the `_prompt` (or its `logic`) you built in notebooks/fe_video_lab.ipynb "
            "into `logic` above. See STUDENT_GUIDE.md.")
    return f"{logic}\n{context}\n{json_contract}"


class VideoVerifier(VerifierBase):
    """Stateless, persistence-based CCTV confirmation of a telemetry stop."""

    # -- one group (GIVEN) --------------------------------------------------
    async def _verify_group(self, group_id: str, t: int, lead: int, tail: int,
                            cars=None) -> dict:
        """One Gemini call over ONE mosaic's window. GIVEN — you built this in the
        notebook as `_call`, so it's handed to you here.

        The move worth noticing: it points Gemini straight at the mp4 in the bucket and
        passes videoMetadata offsets, so the model decodes ONLY [t-lead, t+tail]. No
        download, no ffmpeg, no local disk.
        """
        from google.genai import types
        from shared.gemini import aretry_call

        start, end = max(0, t - lead), t + tail
        cams = self._cams(group_id)
        vpart = types.Part(
            file_data=types.FileData(file_uri=self._uri(group_id), mime_type="video/mp4"),
            video_metadata=types.VideoMetadata(start_offset=f"{start}s",
                                               end_offset=f"{end}s"))
        resp = await aretry_call(lambda: self._client.aio.models.generate_content(
            model=self.model,
            contents=[types.Content(role="user",
                                    parts=[vpart,
                                           types.Part(text=_prompt(cams, t, start, end, cars))])],
            config=types.GenerateContentConfig(temperature=0.2,
                                               response_mime_type="application/json"),
        ), what="verify")

        d = _parse(resp.text)
        d["group"] = group_id
        panel = str(d.get("panel", "none"))
        if panel in PANEL_POS and PANEL_POS.index(panel) < len(cams):
            d["camera"] = cams[PANEL_POS.index(panel)]      # panel -> real camera id
        return d

    # -- the sweep (GIVEN — but SEQUENTIAL on purpose; see Bonus 1) ----------
    async def _sweep(self, t: int, lead: int, tail: int, cars):
        """Sweep every camera group at race-second t; return (per_group, errors).

        It runs the six groups ONE AT A TIME, in a plain for-loop. That works, but it's
        SLOW: six back-to-back ~10s calls ≈ ~60s per stop. These calls are independent
        and I/O-bound — nothing about them needs to be serial. BONUS 1 is to fan them
        out with asyncio.gather and watch ~60s collapse to ~10s. Get a correct verdict
        first, then make it fast.

        `errors` is what lets _aggregate tell a real 'saw nothing' from a check that
        never RAN (auth / provisioning / network).
        """
        per_group, errors = {}, []
        for g in self.groups:                   # <-- Bonus 1: fan these out concurrently
            try:
                r = await self._verify_group(g, t, lead, tail, cars)
            except Exception as e:              # a failing group must not sink the sweep
                logger.warning("group verify failed: %s", e)
                errors.append(_short_error(e))
                continue
            per_group[r["group"]] = r
        return per_group, errors

    # -- the verdict — YOUR CODE -------------------------------------------
    @staticmethod
    def _aggregate(per_group: dict, errors: list | None = None) -> VideoVerdict:
        """Fuse the per-group replies into ONE VideoVerdict.

        `per_group` maps group_id -> the dict `_verify_group` returned. Each carries
        `blockage`, `cleared`, `panel`, `camera`, `confidence`, `what_you_see`,
        `seen_car`. Decide the single verdict for the whole incident.

        START HERE — this is enough to light the board:
          * If ANY group reports a blockage -> state="blocked". Collect the cameras that
            saw it, and take the most-confident reply for description and confidence.
          * Otherwise -> state="unseen".

        COME BACK LATER — the guide sends you back here twice more, once you have seen
        why each of these matters:
          * "error"  — the check could not RUN at all.
          * "cleared" — a car was there and recovered. The false-alarm veto.
        `VideoVerifier._seen_car(reply)` reads the car number the model claims it saw
        (Bonus 2).
        """
        raise NotImplementedError(
            "Fuse the per-group replies into ONE VideoVerdict. Start with blocked vs "
            "unseen — that's enough to light the board. See STUDENT_GUIDE.md.")


def main() -> int:
    return run_cli(VideoVerifier)


if __name__ == "__main__":
    raise SystemExit(main())
