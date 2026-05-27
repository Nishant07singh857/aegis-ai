# backend/scripts/download_models.py
"""
AEGIS AI - Model Download Script
Downloads all required ML models for the AEGIS AI vision pipeline.
Handles YOLO, PaddleOCR, ByteTrack, and other model weights.
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.config import settings
from app.utils.network_utils import Downloader
from app.utils.file_utils import FileUtils

logger = structlog.get_logger("aegis.scripts.download_models")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MODEL DOWNLOAD CONFIGURATIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MODELS_TO_DOWNLOAD = [
    {
        "name": "YOLOv8 Nano",
        "url": "https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8n.pt",
        "path": "models/yolo/yolov8n.pt",
        "required": True,
        "size_mb": 6.2,
    },
    {
        "name": "YOLOv8 Medium",
        "url": "https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8m.pt",
        "path": "models/yolo/yolov8m.pt",
        "required": True,
        "size_mb": 52.0,
    },
    {
        "name": "YOLOv8 Small",
        "url": "https://github.com/ultralytics/assets/releases/download/v8.1.0/yolov8s.pt",
        "path": "models/yolo/yolov8s.pt",
        "required": False,
        "size_mb": 22.5,
    },
    {
        "name": "PaddleOCR Detection",
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_det_infer.tar",
        "path": "models/paddleocr/det",
        "required": True,
        "size_mb": 4.7,
        "is_archive": True,
    },
    {
        "name": "PaddleOCR Recognition",
        "url": "https://paddleocr.bj.bcebos.com/PP-OCRv3/english/en_PP-OCRv3_rec_infer.tar",
        "path": "models/paddleocr/rec",
        "required": True,
        "size_mb": 12.8,
        "is_archive": True,
    },
]


async def download_model(downloader: Downloader, model_info: dict) -> bool:
    """Download a single model"""
    
    name = model_info["name"]
    url = model_info["url"]
    path = os.path.join(settings.DATA_DIR, model_info["path"])
    size_mb = model_info.get("size_mb", 0)
    
    # Check if already downloaded
    if os.path.exists(path) and not model_info.get("is_archive"):
        file_size = os.path.getsize(path) / (1024 * 1024)
        if abs(file_size - size_mb) < size_mb * 0.2:  # Within 20% of expected size
            logger.info(f"✓ {name} already downloaded ({file_size:.1f}MB)")
            return True
    
    logger.info(f"Downloading {name} ({size_mb:.1f}MB)...")
    
    result = await downloader.download(url, path)
    
    if result.success:
        logger.info(f"✓ {name} downloaded ({result.size_mb:.1f}MB in {result.duration_seconds:.1f}s)")
        return True
    else:
        logger.error(f"✗ Failed to download {name}: {result.error}")
        return False


async def download_all():
    """Download all required models"""
    
    downloader = Downloader(max_retries=3)
    
    # Create directories
    FileUtils.ensure_dir(os.path.join(settings.DATA_DIR, "models", "yolo"))
    FileUtils.ensure_dir(os.path.join(settings.DATA_DIR, "models", "paddleocr"))
    
    # Download required models first
    required_models = [m for m in MODELS_TO_DOWNLOAD if m.get("required", True)]
    optional_models = [m for m in MODELS_TO_DOWNLOAD if not m.get("required", True)]
    
    success_count = 0
    fail_count = 0
    
    logger.info(f"Downloading {len(required_models)} required models...")
    
    for model in required_models:
        if await download_model(downloader, model):
            success_count += 1
        else:
            fail_count += 1
            if model.get("required"):
                logger.error(f"Required model '{model['name']}' failed to download!")
    
    # Download optional models
    for model in optional_models:
        if await download_model(downloader, model):
            success_count += 1
        else:
            fail_count += 1
    
    return success_count, fail_count


async def main():
    """Main function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Model Download")
    logger.info("=" * 60)
    
    total_size = sum(m.get("size_mb", 0) for m in MODELS_TO_DOWNLOAD)
    logger.info(f"Total download size: ~{total_size:.0f}MB")
    logger.info(f"Models to download: {len(MODELS_TO_DOWNLOAD)}")
    logger.info("")
    
    success, failed = await download_all()
    
    logger.info("=" * 60)
    logger.info(f"Download complete: {success} success, {failed} failed")
    
    if failed > 0:
        logger.warning("Some models failed to download. Run script again to retry.")
    else:
        logger.info("✅ All models downloaded successfully!")
    
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())