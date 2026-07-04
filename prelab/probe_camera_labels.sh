#!/usr/bin/env bash
# PRE-LAB helper — read each CCTV camera's burned-in label so we can group cameras
# in true track order for the mosaics. Grabs ONE frame from each camera's mid
# block and tiles them into a contact sheet. Run in Cloud Shell (bucket access).
#
# Output: camera_labels_sheet.jpg — read the "T## - CAMxx" overlays off it and
# fill camera_groups.full.json (4 consecutive cameras per 2x2 group, travel order).
#
# Cheap: downloads only a few seconds of each camera, not whole blocks.

set -euo pipefail

BUCKET="${BUCKET:-gs://class-demo/formula-e/footage/berlin_r10/cctv}"
WORK="${WORK:-/tmp/cam_labels}"
SEEK="${SEEK:-600}"          # seconds into the block to sample (mid-race-ish)
mkdir -p "$WORK/frames"

echo ">>> Listing cameras..."
# One representative block per camera (the 2nd block of each — mid race).
mapfile -t BLOCKS < <(gcloud storage ls "$BUCKET/" | grep -E 'Cam[0-9]+-.*\.mp4' | sort | \
    awk -F/ '{print $NF}' | awk -F- '{cam=$1} cam!=last{print; last=cam}')

for f in "${BLOCKS[@]}"; do
    cam="$(echo "$f" | grep -oE 'Cam[0-9]+')"
    echo "    $cam  ($f)"
    # Stream just enough to grab one frame at SEEK; -t 1 keeps the copy tiny.
    tmp="$WORK/${cam}.mp4"
    gcloud storage cat "$BUCKET/$f" 2>/dev/null | \
        ffmpeg -v error -ss "$SEEK" -i pipe:0 -frames:v 1 -q:v 3 "$WORK/frames/${cam}.jpg" -y 2>/dev/null || \
        echo "      (couldn't sample $cam via pipe; try: gcloud storage cp then ffmpeg -ss)"
done

echo ">>> Building contact sheet..."
montage "$WORK"/frames/Cam*.jpg -tile 4x -geometry 480x270+2+2 \
    -background black -fill yellow -label '%f' "$WORK/camera_labels_sheet.jpg"
echo "Done → $WORK/camera_labels_sheet.jpg"
echo "Read each panel's 'T## - CAMxx' overlay and record camera -> turn to build the groups."
