"""1 FPS frame source for the Video Observer.

Extracts frames from a CCTV MP4 at one frame per second (the Live API's video
ceiling, and our design cadence) and maps each to an ABSOLUTE UTC timestamp.
That absolute time is what lets the correlator line a video observation up with
a telemetry stop even though the two never share a clock exactly.

Uses ffmpeg (present in Cloud Shell and locally). No heavy decode deps.
"""
from __future__ import annotations

import glob
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass
class Frame:
    index: int              # 0-based frame number (also seconds from clip start)
    path: str               # JPEG on disk
    ts_utc: datetime        # absolute wall-clock time of this frame


def extract_frames(
    clip_path: str,
    clip_start_utc: datetime,
    *,
    fps: int = 1,
    out_dir: str | None = None,
    quality: int = 3,
) -> list[Frame]:
    """Decode `clip_path` to `fps` frames/sec, timestamped from `clip_start_utc`.

    Args:
      clip_path: source MP4.
      clip_start_utc: wall-clock time the FIRST frame corresponds to. For the
        Berlin R10 hero incident, anchor the clip so frame 0 ≈ the real moment
        (e.g. 13:32:00 UTC) — that anchor is what aligns video with telemetry.
      fps: frames per second to sample (default 1).
      out_dir: where to write JPEGs (a temp dir if None).
      quality: ffmpeg -q:v (lower is better; 2-5 is sensible).

    Returns frames in time order. Raises on ffmpeg failure or empty output.
    """
    if clip_start_utc.tzinfo is None:
        clip_start_utc = clip_start_utc.replace(tzinfo=timezone.utc)
    out_dir = out_dir or tempfile.mkdtemp(prefix="cctv_frames_")
    os.makedirs(out_dir, exist_ok=True)

    cmd = [
        "ffmpeg", "-v", "error", "-i", clip_path,
        "-vf", f"fps={fps}", "-q:v", str(quality),
        os.path.join(out_dir, "f%05d.jpg"),
    ]
    subprocess.run(cmd, check=True)

    paths = sorted(glob.glob(os.path.join(out_dir, "f*.jpg")))
    if not paths:
        raise RuntimeError(f"ffmpeg produced no frames from {clip_path}")

    step = timedelta(seconds=1.0 / fps)
    # ffmpeg names frames from 1; index them from 0 for second-offset math.
    return [
        Frame(index=i, path=p, ts_utc=clip_start_utc + i * step)
        for i, p in enumerate(paths)
    ]
