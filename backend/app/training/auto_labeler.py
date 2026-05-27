"""
AEGIS AI - Auto Labeler for BGMI/PUBG YOLOv8 Training
=======================================================
BGMI UI elements are at FIXED positions (always same location on screen).
We exploit this to auto-generate YOLO-format labels for all frames.

Classes:
  0 = minimap        (top-right corner)
  1 = killfeed       (top-left area)
  2 = health_bar     (bottom-left, current player)
  3 = squad_health   (bottom-right, team panel)
  4 = ammo_counter   (bottom-right)
  5 = zone_timer     (top-center)

YOLO format per line: class x_center y_center width height (all 0-1 normalized)
Image resolution: 2560 x 1440
"""

import shutil
import random
from pathlib import Path
import structlog

logger = structlog.get_logger("aegis.training.auto_labeler")

# ── Image dimensions ─────────────────────────────────────────────────────────
IMG_W = 2560
IMG_H = 1440

def _norm(x, y, w, h):
    """Convert pixel coords → YOLO normalized format"""
    return (
        round(x / IMG_W, 6),
        round(y / IMG_H, 6),
        round(w / IMG_W, 6),
        round(h / IMG_H, 6),
    )

# ── BGMI UI Fixed Regions (pixel coords: cx, cy, w, h) ──────────────────────
# Verified against 2560x1440 BGMI tournament broadcast screenshots
BGMI_UI_LABELS = {
    # class_id: (cx, cy, width, height) in pixels
    0: _norm(2375, 155, 370, 310),   # minimap        - top-right
    1: _norm(270,  110, 480, 180),   # killfeed       - top-left
    2: _norm(390,  1370, 650, 110),  # health_bar     - bottom-left
    3: _norm(2230, 1330, 360, 220),  # squad_health   - bottom-right panel
    4: _norm(2150, 1390, 280,  90),  # ammo_counter   - bottom-right
    5: _norm(1280,  55,  420,  70),  # zone_timer     - top-center
}

CLASS_NAMES = [
    "minimap", "killfeed", "health_bar",
    "squad_health", "ammo_counter", "zone_timer"
]

def generate_label_line(class_id: int) -> str:
    x, y, w, h = BGMI_UI_LABELS[class_id]
    return f"{class_id} {x} {y} {w} {h}"


def run_auto_labeler(
    frames_dir: str = "dataset/frames",
    output_dir: str = "dataset/yolo_training",
    val_split: float = 0.15,
):
    frames_path = Path(frames_dir)
    out = Path(output_dir)

    # Create YOLO folder structure
    for split in ["train", "val"]:
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)

    all_frames = sorted(frames_path.glob("*.jpg"))
    if not all_frames:
        logger.error("No frames found!", path=str(frames_path))
        return

    random.seed(42)
    random.shuffle(all_frames)
    val_count  = max(1, int(len(all_frames) * val_split))
    val_set    = set(f.name for f in all_frames[:val_count])

    label_lines = "\n".join(generate_label_line(cls) for cls in BGMI_UI_LABELS)

    labeled = 0
    for frame in all_frames:
        split = "val" if frame.name in val_set else "train"

        # Copy image
        dst_img = out / "images" / split / frame.name
        shutil.copy2(frame, dst_img)

        # Write label file
        dst_lbl = out / "labels" / split / frame.with_suffix(".txt").name
        dst_lbl.write_text(label_lines)
        labeled += 1

    train_count = labeled - val_count
    logger.info("Auto-labeling complete! ✅",
                total=labeled, train=train_count, val=val_count)
    print("\n✅ Auto-labeling done!")
    print(f"   📁 Output     : {out.resolve()}")
    print(f"   🖼  Train imgs : {train_count}")
    print(f"   🖼  Val imgs   : {val_count}")
    print(f"   🏷  Classes   : {CLASS_NAMES}")

    # Write dataset.yaml for YOLOv8
    yaml_path = out / "dataset.yaml"
    yaml_path.write_text(f"""# AEGIS AI - BGMI YOLOv8 Dataset Config
path: {out.resolve()}
train: images/train
val:   images/val

nc: {len(CLASS_NAMES)}
names: {CLASS_NAMES}
""")
    logger.info("dataset.yaml written", path=str(yaml_path))
    return str(yaml_path)


if __name__ == "__main__":
    run_auto_labeler()
