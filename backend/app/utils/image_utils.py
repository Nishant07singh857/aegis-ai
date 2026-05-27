# backend/app/utils/image_utils.py
"""
AEGIS AI - Image Processing Utilities
Common image operations, preprocessing, and transformations.
"""

from typing import Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import structlog
import numpy as np
import cv2

logger = structlog.get_logger("aegis.utils.image")


class ImageFormat(str, Enum):
    """Supported image formats"""
    BGR = "bgr"
    RGB = "rgb"
    GRAY = "gray"
    HSV = "hsv"
    YUV = "yuv"


@dataclass
class ImageConfig:
    """Image processing configuration"""
    default_size: Tuple[int, int] = (640, 640)
    jpeg_quality: int = 95
    png_compression: int = 3
    max_dimension: int = 4096


class ImageUtils:
    """Image processing utility functions"""
    
    @staticmethod
    def resize(
        image: np.ndarray,
        target_size: Tuple[int, int],
        keep_aspect: bool = True,
        interpolation: int = cv2.INTER_LINEAR,
    ) -> np.ndarray:
        """Resize image with optional aspect ratio preservation"""
        if not keep_aspect:
            return cv2.resize(image, target_size, interpolation=interpolation)
        
        h, w = image.shape[:2]
        target_w, target_h = target_size
        
        scale = min(target_w / w, target_h / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        
        resized = cv2.resize(image, (new_w, new_h), interpolation=interpolation)
        
        # Pad to target size
        pad_w = target_w - new_w
        pad_h = target_h - new_h
        
        top = pad_h // 2
        bottom = pad_h - top
        left = pad_w // 2
        right = pad_w - left
        
        return cv2.copyMakeBorder(resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(0, 0, 0))
    
    @staticmethod
    def convert_color(image: np.ndarray, target_format: ImageFormat) -> np.ndarray:
        """Convert image color space"""
        if target_format == ImageFormat.RGB:
            return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        elif target_format == ImageFormat.GRAY:
            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        elif target_format == ImageFormat.HSV:
            return cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        elif target_format == ImageFormat.YUV:
            return cv2.cvtColor(image, cv2.COLOR_BGR2YUV)
        return image
    
    @staticmethod
    def normalize(
        image: np.ndarray,
        mean: Tuple[float, float, float] = (0.485, 0.456, 0.406),
        std: Tuple[float, float, float] = (0.229, 0.224, 0.225),
    ) -> np.ndarray:
        """Normalize image with mean and std"""
        image = image.astype(np.float32) / 255.0
        mean = np.array(mean, dtype=np.float32)
        std = np.array(std, dtype=np.float32)
        return (image - mean) / std
    
    @staticmethod
    def denormalize(
        image: np.ndarray,
        mean: Tuple[float, float, float] = (0.485, 0.456, 0.406),
        std: Tuple[float, float, float] = (0.229, 0.224, 0.225),
    ) -> np.ndarray:
        """Reverse normalization"""
        mean = np.array(mean, dtype=np.float32)
        std = np.array(std, dtype=np.float32)
        image = image * std + mean
        return np.clip(image * 255, 0, 255).astype(np.uint8)
    
    @staticmethod
    def extract_roi(
        image: np.ndarray,
        x: int, y: int,
        width: int, height: int,
    ) -> np.ndarray:
        """Extract region of interest"""
        h, w = image.shape[:2]
        x = max(0, min(x, w - 1))
        y = max(0, min(y, h - 1))
        width = max(1, min(width, w - x))
        height = max(1, min(height, h - y))
        return image[y:y+height, x:x+width]
    
    @staticmethod
    def apply_watermark(
        image: np.ndarray,
        watermark: np.ndarray,
        position: str = "bottom_right",
        opacity: float = 0.5,
    ) -> np.ndarray:
        """Apply watermark overlay"""
        h, w = image.shape[:2]
        wh, ww = watermark.shape[:2]
        
        positions = {
            "top_left": (10, 10),
            "top_right": (w - ww - 10, 10),
            "bottom_left": (10, h - wh - 10),
            "bottom_right": (w - ww - 10, h - wh - 10),
            "center": ((w - ww) // 2, (h - wh) // 2),
        }
        
        px, py = positions.get(position, (w - ww - 10, h - wh - 10))
        px = max(0, min(px, w - ww))
        py = max(0, min(py, h - wh))
        
        roi = image[py:py+wh, px:px+ww]
        blended = cv2.addWeighted(roi, 1 - opacity, watermark, opacity, 0)
        image[py:py+wh, px:px+ww] = blended
        
        return image
    
    @staticmethod
    def create_thumbnail(
        image: np.ndarray,
        size: Tuple[int, int] = (320, 180),
    ) -> np.ndarray:
        """Create thumbnail"""
        return cv2.resize(image, size, interpolation=cv2.INTER_AREA)
    
    @staticmethod
    def detect_blur(image: np.ndarray) -> float:
        """Detect blur level using Laplacian variance"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return cv2.Laplacian(gray, cv2.CV_64F).var()
    
    @staticmethod
    def calculate_brightness(image: np.ndarray) -> float:
        """Calculate average brightness"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))
    
    @staticmethod
    def auto_contrast(image: np.ndarray, clip_hist_percent: float = 1.0) -> np.ndarray:
        """Auto contrast adjustment"""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        
        accumulator = np.cumsum(hist)
        total = accumulator[-1]
        
        clip = total * clip_hist_percent / 100.0
        min_val = np.searchsorted(accumulator, clip)
        max_val = 255 - np.searchsorted(accumulator[::-1], clip)
        
        if max_val > min_val:
            alpha = 255.0 / (max_val - min_val)
            beta = -min_val * alpha
            return cv2.convertScaleAbs(image, alpha=alpha, beta=beta)
        return image
    
    @staticmethod
    def encode_jpeg(image: np.ndarray, quality: int = 95) -> bytes:
        """Encode image as JPEG bytes"""
        _, buffer = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        return buffer.tobytes()
    
    @staticmethod
    def decode_jpeg(data: bytes) -> np.ndarray:
        """Decode JPEG bytes to image"""
        nparr = np.frombuffer(data, np.uint8)
        return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    @staticmethod
    def save_image(image: np.ndarray, path: str, quality: int = 95) -> bool:
        """Save image to disk"""
        try:
            import os
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return cv2.imwrite(path, image, [cv2.IMWRITE_JPEG_QUALITY, quality])
        except Exception as e:
            logger.error(f"Failed to save image: {e}")
            return False
    
    @staticmethod
    def load_image(path: str) -> Optional[np.ndarray]:
        """Load image from disk"""
        try:
            return cv2.imread(path, cv2.IMREAD_COLOR)
        except Exception as e:
            logger.error(f"Failed to load image: {e}")
            return None