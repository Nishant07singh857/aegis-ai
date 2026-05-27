"""Debug v4 - Extract full frame and find where kill feed actually is."""
import cv2
from pathlib import Path

base_dir = Path(__file__).parent.parent.parent
video_path = base_dir / "dataset" / "videos" / "gameplay_clip.mp4"
out_dir    = base_dir / "dataset" / "debug_crops"
out_dir.mkdir(parents=True, exist_ok=True)

cap = cv2.VideoCapture(str(video_path))

# Save 3 full frames at different timestamps
for target_frame, label in [(60, "1s"), (300, "5s"), (600, "10s")]:
    cap.set(cv2.CAP_PROP_POS_FRAMES, target_frame)
    ret, frame = cap.read()
    if ret:
        # Draw grid lines to identify regions
        h, w = frame.shape[:2]
        # Draw vertical lines at 25%, 50%, 75%
        for pct in [0.25, 0.50, 0.75]:
            x = int(w * pct)
            cv2.line(frame, (x, 0), (x, h), (0, 255, 0), 3)
        # Draw horizontal lines at 25%, 50%, 75%
        for pct in [0.25, 0.50, 0.75]:
            y = int(h * pct)
            cv2.line(frame, (0, y), (w, y), (0, 255, 0), 3)

        # Save full frame (scaled down to 50%)
        small = cv2.resize(frame, (w//2, h//2))
        path = out_dir / f"full_frame_{label}.png"
        cv2.imwrite(str(path), small)
        print(f"Saved: {path}")

cap.release()
print("Done - open the full_frame images to see complete screen layout")
