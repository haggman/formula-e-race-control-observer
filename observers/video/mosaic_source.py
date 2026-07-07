"""MosaicSource — serves 1 FPS mosaic frames indexed by race-second.

The prebuilt mosaic is a 1 FPS mp4 whose frame N is race-second N (it starts at
the green flag). So there is no live "video stream" to run: any consumer that
knows the current race_time_s can serve itself the right frame. This class owns
that mapping — download the group's mosaic (local path or gs://), extract its
frames once, and hand back the frame for a given race-second on demand.

Panel layout comes from the group's manifest entry so the observer's persona can
name the right camera_id.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field


def _ffmpeg_bin() -> str:
    """Path to an ffmpeg binary. Prefer a system ffmpeg; otherwise fall back to
    the static binary bundled by the pip package imageio-ffmpeg — so student lab
    projects don't need a system-level `apt-get install ffmpeg`."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


_GCS = None


def _gcs_client():
    """Cached Storage client. Uses ADC, so it authenticates the same way in Cloud
    Shell (your creds) and on Cloud Run (the service account) — no gcloud CLI."""
    global _GCS
    if _GCS is None:
        from google.cloud import storage
        _GCS = storage.Client()
    return _GCS


@dataclass
class MosaicSource:
    mosaic_ref: str                       # local path or gs:// URI to the group mp4
    group_id: str = ""
    manifest_ref: str = ""                # local path or gs:// to manifest.json
    work_dir: str = ""
    panels: list[dict] = field(default_factory=list)
    _frames: dict[int, str] = field(default_factory=dict)  # race_second -> jpg path
    _max_second: int = -1

    def prepare(self) -> "MosaicSource":
        """Localise the mosaic, extract 1 FPS frames, and load panel layout."""
        if not self.work_dir:
            # FE_WORK_DIR lets a container put frames on a mounted ephemeral disk
            # (so extraction doesn't eat instance memory); defaults to /tmp locally.
            base = os.environ.get("FE_WORK_DIR")
            if base:
                os.makedirs(base, exist_ok=True)
                self.work_dir = tempfile.mkdtemp(prefix="mosaic_", dir=base)
            else:
                self.work_dir = tempfile.mkdtemp(prefix="mosaic_")
        os.makedirs(self.work_dir, exist_ok=True)

        local = self._localise(self.mosaic_ref, os.path.join(self.work_dir, "mosaic.mp4"))
        # Mosaic is already 1 FPS; extract every frame → f00001.jpg (race-second 0).
        subprocess.run(
            [_ffmpeg_bin(), "-v", "error", "-i", local, "-vf", "fps=1", "-q:v", "3",
             os.path.join(self.work_dir, "f%05d.jpg")],
            check=True,
        )
        import glob
        for p in sorted(glob.glob(os.path.join(self.work_dir, "f*.jpg"))):
            n = int(os.path.basename(p)[1:-4]) - 1        # ffmpeg numbers from 1
            self._frames[n] = p
            self._max_second = max(self._max_second, n)
        if not self._frames:
            raise RuntimeError(f"no frames extracted from {self.mosaic_ref}")

        self._load_panels()
        return self

    def frame_path(self, race_second: int) -> str | None:
        """The mosaic frame for a race-second (clamped to available range)."""
        if race_second < 0:
            return None
        n = min(race_second, self._max_second)
        return self._frames.get(n)

    @property
    def max_second(self) -> int:
        return self._max_second

    # -- helpers -------------------------------------------------------------
    def _load_panels(self) -> None:
        if not self.manifest_ref:
            return
        try:
            local = self._localise(self.manifest_ref, os.path.join(self.work_dir, "manifest.json"))
            manifest = json.load(open(local))
            for g in manifest.get("groups", []):
                if not self.group_id or g["group_id"] == self.group_id:
                    self.panels = g.get("panels", [])
                    if not self.group_id:
                        self.group_id = g["group_id"]
                    break
        except Exception:
            self.panels = []                                # persona falls back gracefully

    @staticmethod
    def _localise(ref: str, dest: str) -> str:
        """Bring a gs:// object local via the Storage client (no gcloud CLI, so it
        runs identically in Cloud Shell and a Cloud Run container). Local paths
        pass through unchanged."""
        if ref.startswith("gs://"):
            bucket, _, blob = ref[len("gs://"):].partition("/")
            _gcs_client().bucket(bucket).blob(blob).download_to_filename(dest)
            return dest
        return ref
