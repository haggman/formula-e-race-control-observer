# ============================================================================
# PASTE THIS INTO A NEW CELL in the running notebook (everything it needs is
# already defined: GROUPS, PANELS, local_mp4, GREEN_UTC, cv2, plt).
# Question it answers: is that car STOPPED, or just driving through?
# ============================================================================
from datetime import timedelta

_QUAD = {"TL": (0, 0), "TR": (0, 1), "BL": (1, 0), "BR": (1, 1)}


def _raw_frame(gid, offset_s):
    cap = cv2.VideoCapture(local_mp4(gid))
    fps = cap.get(cv2.CAP_PROP_FPS) or 1.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(offset_s * fps))
    ok, img = cap.read()
    cap.release()
    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB) if ok else None


def panel_crop(gid, panel, offset_s):
    img = _raw_frame(gid, offset_s)
    if img is None:
        return None
    h, w = img.shape[:2]
    r, c = _QUAD[panel]
    return img[r * h // 2:(r + 1) * h // 2, c * w // 2:(c + 1) * w // 2]


def motion_strip(gid_prefix, panel, offsets, title=""):
    """Same camera, several moments. If the car sits in identical pixels
    across 60 seconds, it is stopped. If it moves or vanishes, it isn't."""
    gid = next(g for g in GROUPS if g.startswith(gid_prefix))
    cam = GROUPS[gid][PANELS.index(panel)]
    fig, axes = plt.subplots(1, len(offsets), figsize=(5.2 * len(offsets), 5.4))
    for ax, off in zip(axes, offsets):
        crop = panel_crop(gid, panel, off)
        ax.imshow(crop)
        ax.axis("off")
        utc = GREEN_UTC + timedelta(seconds=off)
        ax.set_title(f"offset {off}s\nclock should read {utc:%H:%M:%S} UTC", fontsize=11)
    fig.suptitle(f"{title}   —   {gid}   panel {panel} = {cam}", fontsize=16)
    plt.tight_layout()
    plt.show()


def stillness(gid_prefix, panel, a, b):
    """Rough number: how much of this panel changed between two moments.
    Compare a panel you believe is static against one with racing cars."""
    gid = next(g for g in GROUPS if g.startswith(gid_prefix))
    ca, cb = panel_crop(gid, panel, a), panel_crop(gid, panel, b)
    d = cv2.absdiff(ca, cb).mean()
    print(f"  {gid[:6]} {panel} ({GROUPS[gid][PANELS.index(panel)]}): "
          f"mean change {a}s -> {b}s = {d:6.2f}")
    return d


# --- THE TEST -------------------------------------------------------------
OFFS = [683, 698, 713, 728, 743, 753]

# 1. The lone car in Cam05. Does it move across the whole window?
motion_strip("grp_02", "TL", OFFS, "Is this Gunther, and is it STOPPED?")

# 2. Control: a panel with the safety-car train. This is what MOVING looks like.
motion_strip("grp_05", "BR", OFFS, "CONTROL - cars that are definitely moving")

# 3. My (probably dead) geometry hypothesis: anything stationary at T13?
motion_strip("grp_05", "BL", OFFS, "Geometry hypothesis - Cam19 T13")

print("\nchange between first and last frame (low = nothing moved):")
stillness("grp_02", "TL", 683, 753)     # suspected stranded car
stillness("grp_05", "BR", 683, 753)     # known moving cars
