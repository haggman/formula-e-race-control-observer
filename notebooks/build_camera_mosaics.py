"""Prebuild the 2x2 camera mosaics the video observer streams.

The video twin of build_frames.py. For each track-ordered group of 4 cameras it
downsamples to 1 FPS, tiles them 2x2 in travel order with a panel label, and
encodes a tiny 1 FPS mp4 — the "given" video-plane artifact students copy into
their own project. Also writes a manifest so the observer knows each mosaic's
start_utc (the alignment anchor) and panel→camera layout.

Alignment: every panel must show the SAME instant. Each panel carries a
`src_offset_s` — the offset into ITS source file at which the group's
`start_utc` occurs — because the CCTV blocks per camera start at slightly
different wall-clock times.

Config (JSON): see camera_groups.example.json. Sources may be local paths or
gs:// URIs (gs:// is copied down first, so run this where the class bucket is
readable — e.g. Cloud Shell).

Usage:
  python notebooks/build_camera_mosaics.py GROUPS.json [OUT_DIR]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
PANEL_W, PANEL_H = 480, 270          # per-panel; mosaic = 960x540
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "mosaics")


def _localize(src: str) -> tuple[str, bool]:
    """Return (local_path, is_temp). Copies gs:// sources down."""
    if src.startswith("gs://"):
        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False).name
        subprocess.run(["gcloud", "storage", "cp", src, tmp], check=True)
        return tmp, True
    return src, False


def _panel_filter(idx: int, label: str) -> str:
    """fps=1 + scale + a small top-left label for one input stream."""
    safe = label.replace(":", r"\:").replace("'", "")
    return (
        f"[{idx}:v]fps=1,scale={PANEL_W}:{PANEL_H},"
        f"drawtext=fontfile={FONT}:text='{safe}':x=6:y=6:fontsize=20:"
        f"fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=4[p{idx}]"
    )


def build_group(group: dict, out_dir: str) -> dict:
    """Build one 2x2 mosaic mp4. Returns its manifest entry."""
    panels = group["panels"]
    if len(panels) != 4:
        raise ValueError(f"group {group['group_id']} needs exactly 4 panels")
    duration = group.get("duration_s")

    locals_, temps = [], []
    for p in panels:
        lp, is_tmp = _localize(p["source"])
        locals_.append(lp)
        if is_tmp:
            temps.append(lp)

    # Build the ffmpeg command: 4 inputs (seeked to their aligned offset) → 2x2.
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for lp, p in zip(locals_, panels):
        cmd += ["-ss", str(p.get("src_offset_s", 0))]
        if duration:
            cmd += ["-t", str(duration)]
        cmd += ["-i", lp]

    fc = ";".join(_panel_filter(i, panels[i]["label"]) for i in range(4))
    fc += ";[p0][p1]hstack[top];[p2][p3]hstack[bot];[top][bot]vstack[out]"
    out_path = os.path.join(out_dir, f"{group['group_id']}.mp4")
    cmd += ["-filter_complex", fc, "-map", "[out]", "-r", "1",
            "-c:v", "libx264", "-crf", "28", "-an", out_path]
    subprocess.run(cmd, check=True)

    for t in temps:
        os.unlink(t)

    size = os.path.getsize(out_path)
    print(f"  {group['group_id']}: {out_path}  ({size/1e6:.2f} MB)")
    return {
        "group_id": group["group_id"],
        "file": os.path.basename(out_path),
        "start_utc": group["start_utc"],
        "grid": "2x2",
        "layout": ["top-left", "top-right", "bottom-left", "bottom-right"],
        "panels": [
            {"panel": lay, "camera_id": p["camera_id"], "label": p["label"]}
            for lay, p in zip(
                ["top-left", "top-right", "bottom-left", "bottom-right"], panels)
        ],
        "size_bytes": size,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    with open(sys.argv[1]) as f:
        cfg = json.load(f)
    out_dir = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_OUT
    os.makedirs(out_dir, exist_ok=True)

    entries = [build_group(g, out_dir) for g in cfg["groups"]]
    manifest = {"race_id": cfg.get("race_id", "berlin_2024_r10"), "groups": entries}
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote {len(entries)} mosaic(s) + manifest → {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
