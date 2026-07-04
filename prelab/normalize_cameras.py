"""PRE-LAB: normalize every CCTV camera to the race window, then emit the mosaic
groups config. For full-race (continuous) mosaics.

Why this exists: each camera's 30-minute CCTV blocks start at DIFFERENT local
times, so we can't just point the mosaic builder at raw blocks for a long window.
This normalizes each camera to ONE aligned 1 FPS clip covering the whole race
(race start → end), so every panel then lines up at offset 0.

Efficiency: reads each block directly from the bucket over HTTPS with ffmpeg
range seeks (gs://class-demo is public-read), so it transfers only the needed
segment at 1 FPS — no 120 GB of downloads, no Cloud Shell disk pressure. If the
bucket isn't public in your setup, pass --auth to use signed URLs via gcloud.

Output:
  <out>/cam_XX_1fps.mp4      one aligned 1 FPS clip per camera
  notebooks/camera_groups.full.json   groups of 4 consecutive cameras (offset 0)

Usage (Cloud Shell):
  python prelab/normalize_cameras.py --order prelab/camera_order.txt

`camera_order.txt`: the 24 camera IDs, one per line, in TRACK order (from the
label probe). Groups are consecutive fours in that order.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

BUCKET_GS = "gs://class-demo/formula-e/footage/berlin_r10/cctv"
BUCKET_HTTPS = "https://storage.googleapis.com/class-demo/formula-e/footage/berlin_r10/cctv"
DATE = "12052024"                          # ddmmyyyy in the filenames
TZ_OFFSET_H = 2                            # Berlin CEST = UTC+2 in May

# Race window (UTC). Green flag 13:04:00; end ~13:52:00 → 2880 s @ 1 FPS.
RACE_START_UTC = datetime(2024, 5, 12, 13, 4, 0, tzinfo=timezone.utc)
RACE_END_UTC = datetime(2024, 5, 12, 13, 52, 0, tzinfo=timezone.utc)
PANEL_W, PANEL_H = 480, 270

FNAME_RE = re.compile(r"(Cam\d+)-\d+-(\d{6})-(\d{6})\.mp4")


def _local_to_utc(hhmmss: str) -> datetime:
    """A block's HHMMSS local time → UTC datetime on race day."""
    t = datetime.strptime(hhmmss, "%H%M%S")
    return datetime(2024, 5, 12, t.hour, t.minute, t.second,
                    tzinfo=timezone.utc) - timedelta(hours=TZ_OFFSET_H)


def list_blocks() -> dict[str, list[tuple[str, datetime, datetime]]]:
    """cam_id → [(filename, block_start_utc, block_end_utc), ...] sorted."""
    out = subprocess.run(["gcloud", "storage", "ls", f"{BUCKET_GS}/"],
                         capture_output=True, text=True, check=True).stdout
    blocks: dict[str, list[tuple[str, datetime, datetime]]] = {}
    for line in out.splitlines():
        m = FNAME_RE.search(line)
        if not m:
            continue
        cam, start, end = m.group(1), m.group(2), m.group(3)
        s, e = _local_to_utc(start), _local_to_utc(end)
        blocks.setdefault(cam, []).append((os.path.basename(line.strip()), s, e))
    for cam in blocks:
        blocks[cam].sort(key=lambda b: b[1])
    return blocks


def normalize_camera(cam: str, blocks: list, out_dir: str, use_auth: bool) -> str | None:
    """Extract the race-overlapping 1 FPS segments from a camera's blocks and
    concat them into one aligned clip starting at race start."""
    segs: list[str] = []
    seg_paths: list[str] = []
    for i, (fname, b_start, b_end) in enumerate(blocks):
        seg_start = max(RACE_START_UTC, b_start)
        seg_end = min(RACE_END_UTC, b_end)
        if seg_end <= seg_start:
            continue                                    # block doesn't overlap the race
        offset = (seg_start - b_start).total_seconds()  # into the block
        dur = (seg_end - seg_start).total_seconds()
        src = (f"{BUCKET_HTTPS}/{fname}" if not use_auth
               else subprocess.run(["gcloud", "storage", "sign-url", f"{BUCKET_GS}/{fname}",
                                    "--duration=1h", "--format=value(signed_url)"],
                                   capture_output=True, text=True, check=True).stdout.strip())
        seg = os.path.join(out_dir, f"{cam}_seg{i}.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", str(offset),
                        "-i", src, "-t", str(dur),
                        "-vf", f"fps=1,scale={PANEL_W}:{PANEL_H}",
                        "-c:v", "libx264", "-crf", "28", "-an", seg], check=True)
        segs.append(f"file '{os.path.basename(seg)}'")
        seg_paths.append(seg)

    if not seg_paths:
        print(f"  {cam}: no race-overlapping footage — skipped")
        return None
    listfile = os.path.join(out_dir, f"{cam}.concat.txt")
    with open(listfile, "w") as f:
        f.write("\n".join(segs))
    out = os.path.join(out_dir, f"{cam.lower()}_1fps.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
                    "-i", listfile, "-c", "copy", out], check=True)
    for p in seg_paths:
        os.unlink(p)
    os.unlink(listfile)
    print(f"  {cam}: {out}  ({os.path.getsize(out)/1e6:.1f} MB)")
    return out


def emit_groups_config(order: list[str], clips: dict[str, str], labels: dict[str, str],
                       cfg_path: str) -> None:
    """Group consecutive fours into 2×2 mosaics; sources are the aligned clips
    (offset 0, start_utc = race start)."""
    groups = []
    for gi in range(0, len(order) - len(order) % 4, 4):
        four = order[gi:gi + 4]
        groups.append({
            "group_id": f"grp_{gi//4 + 1:02d}_" + "_".join(c.lower() for c in four),
            "start_utc": RACE_START_UTC.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "panels": [
                {"camera_id": c, "label": labels.get(c, c),
                 "source": clips[c], "src_offset_s": 0}
                for c in four if c in clips
            ],
        })
    with open(cfg_path, "w") as f:
        json.dump({"race_id": "berlin_2024_r10", "groups": groups}, f, indent=2)
    print(f"Wrote {len(groups)} groups → {cfg_path}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", required=True,
                    help="file of camera IDs in track order, one per line "
                         "(optionally 'CamXX,LABEL')")
    ap.add_argument("--out-dir", default="/tmp/cam_1fps")
    ap.add_argument("--config", default="notebooks/camera_groups.full.json")
    ap.add_argument("--auth", action="store_true",
                    help="use signed URLs instead of public HTTPS")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    order, labels = [], {}
    for line in open(args.order):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cam, _, label = line.partition(",")
        order.append(cam.strip())
        labels[cam.strip()] = label.strip() or cam.strip()

    blocks = list_blocks()
    clips: dict[str, str] = {}
    for cam in order:
        if cam not in blocks:
            print(f"  {cam}: no blocks found — check the ID")
            continue
        clip = normalize_camera(cam, blocks[cam], args.out_dir, args.auth)
        if clip:
            clips[cam] = clip

    emit_groups_config(order, clips, labels, args.config)
    print(f"\nNext: python notebooks/build_camera_mosaics.py {args.config} /tmp/mosaics")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
