import os
import cv2
import glob
import structlog
import numpy as np
from pathlib import Path

logger = structlog.get_logger("aegis.training.cleaner")

class DatasetCleaner:
    """
    AEGIS AI - Automated Dataset Curation Engine
    Scans extracted frames and automatically deletes "useless" data
    (Waiting screens, Lobby, Ads, Blank screens) so you only train on REAL gameplay.
    """
    def __init__(self, frames_dir: str = "dataset/frames"):
        self.frames_dir = Path(frames_dir)
        
    def is_gameplay_frame(self, frame) -> bool:
        """
        Uses heuristics to determine if a frame is actual BGMI gameplay.
        Instead of a heavy AI model, we use OpenCV structural analysis:
        - Real gameplay has a high variance of edges and colors.
        - Waiting screens usually have large static, flat color areas or dark backgrounds.
        """
        # Convert to grayscale for structural analysis
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # 1. Edge Density Test (Gameplay has lots of grass, buildings, UI edges)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = np.sum(edges) / 255.0 / (frame.shape[0] * frame.shape[1])
        
        # 2. Brightness / Contrast Test
        mean_brightness = np.mean(gray)
        
        # If it's too dark (e.g., black transition screen) or has very few edges (flat logo screen)
        if edge_density < 0.02 or mean_brightness < 15:
            return False
            
        # In a real enterprise system, we would also run a lightweight OCR on the top-left 
        # to look for "ALIVE" or "KILLS" text, or check for the minimap shape in top-right.
        return True

    def clean_dataset(self):
        """Scans the frames directory and deletes useless frames."""
        if not self.frames_dir.exists():
            logger.error("Frames directory does not exist", path=str(self.frames_dir))
            return
            
        image_files = glob.glob(str(self.frames_dir / "*.jpg"))
        if not image_files:
            logger.warning("No images found to clean.")
            return
            
        logger.info("Starting Automated Dataset Cleaning...", total_files=len(image_files))
        
        deleted_count = 0
        kept_count = 0
        
        for file_path in image_files:
            frame = cv2.imread(file_path)
            if frame is None:
                os.remove(file_path)
                deleted_count += 1
                continue
                
            if not self.is_gameplay_frame(frame):
                os.remove(file_path)
                deleted_count += 1
            else:
                kept_count += 1
                
        logger.info("Dataset Cleanup Complete!", 
                    total_scanned=len(image_files), 
                    kept_gameplay_frames=kept_count, 
                    deleted_useless_frames=deleted_count)

# ── Usage Example ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    cleaner = DatasetCleaner(frames_dir="/Users/nishantsingh/Downloads/aegis-ai-complete/backend/dataset/frames")
    cleaner.clean_dataset()
