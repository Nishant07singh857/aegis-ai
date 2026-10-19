#!/usr/bin/env python3
"""Auto-label YouTube frames for killfeed detection (contour-based, no OCR).

Killfeed is always top-left (per Visual Bible: x 0-18%, y 4-20%).
Detect text-like horizontal blobs in that region using MSER/contours.
Outputs YOLO-format dataset.
"""
import os
import shutil
from pathlib import Path
import cv2
import numpy as np

# Killfeed region (from Visual Bible, as fractions)
KF_X1, KF_X2 = 0.00, 0.20  # x: 0-20% (slightly wider than Bible for safety)
KF_Y1, KF_Y2 = 0.03, 0.22  # y: 3-22%

def is_gameplay(img):
    """Check if frame is actual gameplay (not menu/graphic/pre-show).
    Gameplay: 3D terrain has green dominance (grass) + high texture.
    Graphics: dark UI backgrounds, less green.
    """
    h, w = img.shape[:2]
    cx1, cx2 = int(w * 0.3), int(w * 0.7)
    cy1, cy2 = int(h * 0.3), int(h * 0.7)
    center = img[cy1:cy2, cx1:cx2]
    
    # Green dominance (Erangel/Miramar terrain)
    mean_b, mean_g, mean_r = cv2.mean(center)[:3]
    green_dominant = mean_g > mean_b and mean_g > mean_r * 0.9
    
    # Texture (3D scenes have varied texture)
    gray = cv2.cvtColor(center, cv2.COLOR_BGR2GRAY)
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    
    return green_dominant and lap_var > 50


def detect_killfeed_region(img):
    """Detect killfeed-like text blobs in top-left region.
    Returns (bbox, confidence) or (None, 0.0).
    bbox = (x, y, w, h) in pixels.
    """
    h, w = img.shape[:2]
    
    # Crop to killfeed region
    x1, x2 = int(w * KF_X1), int(w * KF_X2)
    y1, y2 = int(h * KF_Y1), int(h * KF_Y2)
    roi = img[y1:y2, x1:x2]
    
    # Convert to grayscale
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    
    # Use MSER to find text-like regions
    mser = cv2.MSER_create()
    regions, _ = mser.detectRegions(gray)
    
    if not regions:
        return None, 0.0
    
    # Filter for horizontal text-like blobs
    # Killfeed entries are wide and short
    candidates = []
    for pts in regions:
        x, y, bw, bh = cv2.boundingRect(pts)
        aspect = bw / max(bh, 1)
        area = bw * bh
        # Killfeed text: wide (aspect > 2), reasonable size
        if aspect > 2.0 and aspect < 20 and area > 200 and area < 8000:
            # Convert to full-image coords
            candidates.append((x1 + x, y1 + y, bw, bh))
    
    if not candidates:
        return None, 0.0
    
    # Merge overlapping boxes into one killfeed region
    # Simple: take bounding box of all candidates
    xs = [c[0] for c in candidates]
    ys = [c[1] for c in candidates]
    xe = [c[0] + c[2] for c in candidates]
    ye = [c[1] + c[3] for c in candidates]
    
    bx1, by1 = min(xs), min(ys)
    bx2, by2 = max(xe), max(ye)
    
    # Confidence based on number and quality of candidates
    # More text blobs = more likely real killfeed
    conf = min(0.95, 0.3 + len(candidates) * 0.15)
    
    # Require at least 2 text-like regions for positive
    # (single blob could be noise)
    if len(candidates) < 2:
        return None, conf * 0.5  # low confidence
    
    return (bx1, by1, bx2 - bx1, by2 - by1), conf


def label_frames(input_dir: Path, output_base: Path):
    images_train = output_base / "images" / "train"
    labels_train = output_base / "labels" / "train"
    images_val = output_base / "images" / "val"
    labels_val = output_base / "labels" / "val"
    images_review = output_base / "images" / "review"
    
    for d in [images_train, labels_train, images_val, labels_val, images_review]:
        d.mkdir(parents=True, exist_ok=True)
    
    frames = sorted(input_dir.glob("frame_*.jpg"))
    stats = {"positive": 0, "negative": 0, "review": 0, "errors": 0}
    
    HIGH_CONF = 0.70
    LOW_CONF = 0.30
    
    for i, fp in enumerate(frames):
        try:
            img = cv2.imread(str(fp))
            if img is None:
                stats["errors"] += 1
                continue
            
            # Skip non-gameplay frames (menus, graphics, pre-show)
            if not is_gameplay(img):
                # Non-gameplay → negative (not killfeed training data)
                dest_img = (images_val if is_val else images_train) / fp.name
                dest_lbl = (labels_val if is_val else labels_train) / (fp.stem + ".txt")
                shutil.copy2(fp, dest_img)
                dest_lbl.touch()
                stats["negative"] += 1
                continue
            
            bbox, conf = detect_killfeed_region(img)
            is_val = (i % 10 == 9)
            
            if bbox and conf >= HIGH_CONF:
                dest_img = (images_val if is_val else images_train) / fp.name
                dest_lbl = (labels_val if is_val else labels_train) / (fp.stem + ".txt")
                shutil.copy2(fp, dest_img)
                h, w = img.shape[:2]
                bx, by, bw, bh = bbox
                x_c = (bx + bw / 2) / w
                y_c = (by + bh / 2) / h
                nw, nh = bw / w, bh / h
                with open(dest_lbl, "w") as f:
                    f.write(f"0 {x_c:.6f} {y_c:.6f} {nw:.6f} {nh:.6f}\n")
                stats["positive"] += 1
            elif conf < LOW_CONF:
                dest_img = (images_val if is_val else images_train) / fp.name
                dest_lbl = (labels_val if is_val else labels_train) / (fp.stem + ".txt")
                shutil.copy2(fp, dest_img)
                dest_lbl.touch()
                stats["negative"] += 1
            else:
                shutil.copy2(fp, images_review / fp.name)
                stats["review"] += 1
                
        except Exception as e:
            stats["errors"] += 1
        
        if (i + 1) % 2000 == 0:
            print(f"  {input_dir.name}: {i+1}/{len(frames)} "
                  f"(pos={stats['positive']} neg={stats['negative']} rev={stats['review']})", flush=True)
    
    return stats


def main():
    base = Path.home() / "workspace/aegis-ai"
    frames_base = base / "data" / "frames"
    output_base = base / "data" / "youtube_dataset"
    
    all_stats = {}
    for vod in ["bmsd2026_gf_day2", "bmsd2026_gf_day3", "pmgc2025_gf_day1"]:
        input_dir = frames_base / vod
        if not input_dir.exists():
            print(f"Skip {vod}: not found")
            continue
        n = len(list(input_dir.glob("frame_*.jpg")))
        print(f"\nLabeling {vod} ({n} frames)...", flush=True)
        stats = label_frames(input_dir, output_base)
        all_stats[vod] = stats
        print(f"  Done: {stats}", flush=True)
    
    print("\n=== FINAL ===")
    for vod, s in all_stats.items():
        print(f"  {vod}: {s}")


if __name__ == "__main__":
    main()
