"""VideoVerifier — REFERENCE (the answer key).

Same file, same layout as starter/video_verifier/verifier.py, finished. The plumbing
both packages share lives next door in `_given.py`.

Differences from the starter, all deliberate:
  * `_prompt`   is written.
  * `_sweep`    is CONCURRENT (the starter's is sequential — that's Bonus 1).
  * `_aggregate` honours all four states, including the `cleared` veto and honest `error`.
"""
from __future__ import annotations

import asyncio

from ._given import (
    LEAD_S, TAIL_S, LIVERIES, PANEL_POS,
    VideoVerdict, VerifierBase,
    _parse, _short_error, logger, run_cli,
)


def _prompt(cams: list[str], t: int, start: int, end: int, cars=None) -> str:
    tl, tr, bl, br = (cams + ["?", "?", "?", "?"])[:4]
    hint = ""
    if cars:
        who = ", ".join(f"#{c} ({LIVERIES.get(int(c), 'livery unknown')})" for c in cars)
        hint = (f"Telemetry says the car(s) likely involved are: {who}. If you see a stopped car, "
                "use its LIVERY/colour AND its car NUMBER (if you can clearly read it) to say whether "
                "it matches. If you cannot clearly read a number, do NOT guess one — just describe the "
                "colour/livery you see.\n")
    return (
        "You are a race-control video verifier deciding whether a SAFETY CAR is warranted.\n"
        f"Telemetry flagged a car possibly stopped near here around race time ~{t}s.\n"
        + hint +
        f"This is a ~{end - start}s CCTV clip — a 2x2 mosaic of four cameras: "
        f"TL={tl}, TR={tr}, BL={bl}, BR={br} — covering that moment.\n"
        "Judge the TRACK STATE by the END of the clip (the safety call is about the track, not which "
        "car it is):\n"
        "- A car STILL stopped/stranded on or beside the racing line at the end (a persistent "
        "obstruction, maybe with marshals or a recovery vehicle): blockage=true, cleared=false.\n"
        "- A car appeared but DROVE AWAY / was recovered / the line is clear by the end: "
        "blockage=false, cleared=true.\n"
        "- No stopped car at any point: blockage=false, cleared=false.\n"
        "Note whether other cars are moving (feed live).\n"
        'Respond with a single JSON object: {"blockage": bool, "cleared": bool, '
        '"panel": "TL|TR|BL|BR|none", "feed_live": bool, '
        '"seen_car": <the stopped car\'s number if you can clearly read it, else null>, '
        '"what_you_see": str, "confidence": number}'
    )


class VideoVerifier(VerifierBase):
    """Stateless, persistence-based CCTV confirmation of a telemetry stop."""

    async def _verify_group(self, group_id: str, t: int, lead: int, tail: int,
                            cars=None) -> dict:
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
            d["camera"] = cams[PANEL_POS.index(panel)]
        return d

    async def _sweep(self, t: int, lead: int, tail: int, cars):
        """One CONCURRENT all-groups sweep at race-second t  (the starter's is a
        sequential for-loop — making it concurrent is Bonus 1).

        Returns (per_group_replies, errors); `errors` lets the caller tell a real
        'saw nothing' from a check that never RAN (auth / provisioning / network).
        """
        results = await asyncio.gather(
            *[self._verify_group(g, t, lead, tail, cars) for g in self.groups],
            return_exceptions=True)
        per_group, errors = {}, []
        for r in results:
            if isinstance(r, Exception):
                logger.warning("group verify failed: %s", r)
                errors.append(_short_error(r))
                continue
            per_group[r["group"]] = r
        return per_group, errors

    @staticmethod
    def _aggregate(per_group: dict, errors: list | None = None) -> VideoVerdict:
        blocked = [r for r in per_group.values() if r.get("blockage")]
        cleared = [r for r in per_group.values() if r.get("cleared")]
        if blocked:
            best = max(blocked, key=lambda r: r.get("confidence", 0) or 0)
            cams = sorted({r.get("camera") for r in blocked if r.get("camera")})
            return VideoVerdict(state="blocked", cameras=cams,
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group,
                                identified=VideoVerifier._seen_car(best))
        if cleared:
            best = max(cleared, key=lambda r: r.get("confidence", 0) or 0)
            return VideoVerdict(state="cleared",
                                description=str(best.get("what_you_see", "")),
                                confidence=float(best.get("confidence", 0) or 0),
                                per_group=per_group,
                                identified=VideoVerifier._seen_car(best))
        # Nothing blocked or cleared. If NO group even ran, that's an outage, not a
        # clean "no view" — surface it so the console can say so.
        if not per_group and errors:
            return VideoVerdict(state="error", per_group=per_group,
                                description=errors[0], error=errors[0])
        return VideoVerdict(state="unseen", per_group=per_group)


def main() -> int:
    return run_cli(VideoVerifier)


if __name__ == "__main__":
    raise SystemExit(main())
