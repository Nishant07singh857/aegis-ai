# backend/app/vision/models/paddleocr_service.py
"""
AEGIS AI - PaddleOCR Service
Production wrapper for PaddleOCR text recognition.
Optimized for BGMI kill feed, HUD elements, and in-game text.
"""

import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.ocr")


@dataclass
class OCRConfig:
    """PaddleOCR configuration"""
    # Language
    lang: str = "en"
    use_angle_cls: bool = True
    
    # Detection
    det_db_thresh: float = 0.3
    det_db_box_thresh: float = 0.5
    det_db_unclip_ratio: float = 1.6
    det_limit_side_len: int = 960
    
    # Recognition
    rec_batch_num: int = 6
    rec_char_dict_path: str = ""
    
    # Performance
    use_gpu: bool = True
    gpu_id: int = 0
    enable_mkldnn: bool = False
    
    # Post-processing
    min_confidence: float = 0.5
    merge_boxes: bool = True
    merge_distance: float = 10.0
    
    # BGMI-specific
    kill_feed_mode: bool = False  # Optimize for kill feed text
    hud_mode: bool = False       # Optimize for HUD elements
    
    # Regions of interest
    roi: Optional[Tuple[int, int, int, int]] = None  # (x, y, w, h)
    
    # Caching
    cache_results: bool = True
    cache_ttl: float = 0.5  # Seconds


@dataclass
class OCRTextLine:
    """Single OCR text line result"""
    text: str
    confidence: float
    bbox: Tuple[float, float, float, float]  # (x, y, w, h) in pixels
    angle: float = 0.0
    
    @property
    def center(self) -> Tuple[float, float]:
        return (self.bbox[0] + self.bbox[2] / 2, self.bbox[1] + self.bbox[3] / 2)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "confidence": round(self.confidence, 3),
            "bbox": [round(x, 1) for x in self.bbox],
            "angle": round(self.angle, 2),
        }


@dataclass
class OCRResult:
    """Complete OCR result"""
    lines: List[OCRTextLine] = field(default_factory=list)
    full_text: str = ""
    processing_time_ms: float = 0.0
    detection_time_ms: float = 0.0
    recognition_time_ms: float = 0.0
    image_shape: Tuple[int, int] = (0, 0)
    timestamp: float = field(default_factory=time.time)
    
    @property
    def count(self) -> int:
        return len(self.lines)
    
    def get_text(self, min_confidence: float = 0.0) -> str:
        """Get all text above confidence threshold"""
        return " ".join([
            line.text for line in self.lines
            if line.confidence >= min_confidence
        ])
    
    def get_lines_by_confidence(self, threshold: float = 0.5) -> List[OCRTextLine]:
        """Get lines above confidence threshold"""
        return [line for line in self.lines if line.confidence >= threshold]
    
    def find_text(self, search: str) -> List[OCRTextLine]:
        """Find lines containing specific text"""
        search_lower = search.lower()
        return [line for line in self.lines if search_lower in line.text.lower()]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "full_text": self.full_text,
            "lines": [line.to_dict() for line in self.lines],
            "timing": {
                "total_ms": round(self.processing_time_ms, 2),
                "detection_ms": round(self.detection_time_ms, 2),
                "recognition_ms": round(self.recognition_time_ms, 2),
            },
        }


class PaddleOCRService:
    """
    Production PaddleOCR service for BGMI text recognition.
    
    Features:
    - Optimized for BGMI kill feed text
    - HUD element reading
    - Angle correction
    - Text merging for multi-line
    - Region of interest support
    - Result caching
    - Thread-safe inference
    """
    
    def __init__(self, config: Optional[OCRConfig] = None):
        self.config = config or OCRConfig()
        self.ocr: Optional[Any] = None
        
        # Thread safety
        self._lock = threading.Lock()
        self._is_loaded = False
        
        # Cache
        self._cache: Dict[str, Tuple[float, OCRResult]] = {}
        
        # Statistics
        self._total_inferences = 0
        self._total_processing_time = 0.0
        self._errors = 0
        
        self._log = logger.bind(component="paddleocr_service")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_model(self) -> bool:
        """Load PaddleOCR model"""
        
        try:
            from paddleocr import PaddleOCR
            
            with self._lock:
                self._log.info("Loading PaddleOCR model...")
                
                self.ocr = PaddleOCR(
                    use_angle_cls=self.config.use_angle_cls,
                    lang=self.config.lang,
                    use_gpu=self.config.use_gpu,
                    gpu_id=self.config.gpu_id,
                    enable_mkldnn=self.config.enable_mkldnn,
                    det_db_thresh=self.config.det_db_thresh,
                    det_db_box_thresh=self.config.det_db_box_thresh,
                    det_db_unclip_ratio=self.config.det_db_unclip_ratio,
                    det_limit_side_len=self.config.det_limit_side_len,
                    rec_batch_num=self.config.rec_batch_num,
                    show_log=False,
                )
                
                self._is_loaded = True
                
                # Warmup
                self._warmup()
                
                self._log.info("PaddleOCR model loaded")
                return True
                
        except ImportError:
            self._log.error("PaddleOCR not installed")
            return False
        except Exception as e:
            self._log.error(f"Failed to load PaddleOCR: {e}")
            self._errors += 1
            return False
    
    def _warmup(self) -> None:
        """Warm up OCR with dummy image"""
        
        if not self._is_loaded or self.ocr is None:
            return
        
        try:
            dummy = np.zeros((100, 300, 3), dtype=np.uint8)
            self.ocr.ocr(dummy, cls=False)
            self._log.debug("OCR model warmed up")
        except Exception as e:
            self._log.warning(f"OCR warmup failed: {e}")
    
    def unload_model(self) -> None:
        """Unload OCR model"""
        
        with self._lock:
            self.ocr = None
            self._is_loaded = False
            self._cache.clear()
            self._log.info("PaddleOCR model unloaded")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INFERENCE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def recognize(
        self,
        image: np.ndarray,
        roi: Optional[Tuple[int, int, int, int]] = None,
        use_cache: bool = True,
    ) -> OCRResult:
        """
        Recognize text in an image.
        
        Args:
            image: Input image (BGR format)
            roi: Region of interest (x, y, w, h)
            use_cache: Whether to use result caching
            
        Returns:
            OCRResult with recognized text
        """
        
        if not self._is_loaded or self.ocr is None:
            self._log.error("OCR model not loaded")
            return OCRResult()
        
        start_time = time.time()
        
        # Extract ROI if specified
        target = image
        if roi or self.config.roi:
            r = roi or self.config.roi
            if r:
                x, y, w, h = r
                target = image[y:y+h, x:x+w]
        
        # Check cache
        cache_key = ""
        if use_cache and self.config.cache_results:
            cache_key = self._get_cache_key(target)
            cached = self._cache.get(cache_key)
            if cached:
                cache_time, cache_result = cached
                if time.time() - cache_time < self.config.cache_ttl:
                    return cache_result
        
        try:
            with self._lock:
                # Run OCR
                raw_results = self.ocr.ocr(target, cls=self.config.use_angle_cls)
            
            # Parse results
            lines = self._parse_results(raw_results)
            
            # Merge nearby text if enabled
            if self.config.merge_boxes:
                lines = self._merge_lines(lines)
            
            # Build full text
            full_text = " ".join([line.text for line in lines])
            
            processing_time = (time.time() - start_time) * 1000
            
            result = OCRResult(
                lines=lines,
                full_text=full_text,
                processing_time_ms=processing_time,
                image_shape=(target.shape[1], target.shape[0]),
            )
            
            # Update cache
            if cache_key:
                self._cache[cache_key] = (time.time(), result)
                # Limit cache size
                if len(self._cache) > 50:
                    oldest = min(self._cache.keys(), key=lambda k: self._cache[k][0])
                    del self._cache[oldest]
            
            # Update statistics
            self._total_inferences += 1
            self._total_processing_time += processing_time
            
            return result
            
        except Exception as e:
            self._log.error(f"OCR error: {e}")
            self._errors += 1
            return OCRResult(
                processing_time_ms=(time.time() - start_time) * 1000,
                image_shape=(target.shape[1], target.shape[0]) if target is not None else (0, 0),
            )
    
    def recognize_region(
        self,
        image: np.ndarray,
        x: int, y: int, w: int, h: int,
    ) -> OCRResult:
        """Recognize text in a specific region"""
        return self.recognize(image, roi=(x, y, w, h))
    
    def recognize_kill_feed(self, image: np.ndarray) -> OCRResult:
        """Recognize text optimized for kill feed"""
        # Kill feed is typically on the right side
        h, w = image.shape[:2]
        kill_feed_roi = (int(w * 0.75), int(h * 0.05), int(w * 0.23), int(h * 0.40))
        return self.recognize(image, roi=kill_feed_roi)
    
    def _parse_results(self, raw_results: List[Any]) -> List[OCRTextLine]:
        """Parse raw OCR results into standard format"""
        
        lines = []
        
        if not raw_results or not raw_results[0]:
            return lines
        
        for detection in raw_results[0]:
            if detection is None or len(detection) < 2:
                continue
            
            bbox_points = detection[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
            text_info = detection[1]     # (text, confidence)
            
            text = text_info[0] if len(text_info) > 0 else ""
            confidence = text_info[1] if len(text_info) > 1 else 0.0
            
            # Convert 4-point bbox to (x, y, w, h)
            if bbox_points and len(bbox_points) >= 4:
                xs = [p[0] for p in bbox_points]
                ys = [p[1] for p in bbox_points]
                
                x = min(xs)
                y = min(ys)
                w = max(xs) - x
                h = max(ys) - y
                
                line = OCRTextLine(
                    text=text,
                    confidence=confidence,
                    bbox=(x, y, w, h),
                )
                
                if confidence >= self.config.min_confidence:
                    lines.append(line)
        
        return lines
    
    def _merge_lines(self, lines: List[OCRTextLine]) -> List[OCRTextLine]:
        """Merge nearby text lines"""
        
        if len(lines) < 2:
            return lines
        
        merged = []
        used = set()
        
        for i, line1 in enumerate(lines):
            if i in used:
                continue
            
            current = line1
            
            for j, line2 in enumerate(lines):
                if j <= i or j in used:
                    continue
                
                # Check if lines are close horizontally
                dist = abs(current.center[1] - line2.center[1])
                if dist < self.config.merge_distance:
                    # Merge: combine text
                    if line2.center[0] > current.center[0]:
                        new_text = current.text + " " + line2.text
                    else:
                        new_text = line2.text + " " + current.text
                    
                    new_conf = (current.confidence + line2.confidence) / 2
                    
                    # Expand bbox
                    new_x = min(current.bbox[0], line2.bbox[0])
                    new_y = min(current.bbox[1], line2.bbox[1])
                    new_w = max(current.bbox[0] + current.bbox[2], line2.bbox[0] + line2.bbox[2]) - new_x
                    new_h = max(current.bbox[1] + current.bbox[3], line2.bbox[1] + line2.bbox[3]) - new_y
                    
                    current = OCRTextLine(
                        text=new_text,
                        confidence=new_conf,
                        bbox=(new_x, new_y, new_w, new_h),
                    )
                    used.add(j)
            
            merged.append(current)
            used.add(i)
        
        return merged
    
    def _get_cache_key(self, image: np.ndarray) -> str:
        """Generate cache key for image"""
        import hashlib
        return hashlib.md5(image.tobytes()).hexdigest()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def is_loaded(self) -> bool:
        """Check if model is loaded"""
        return self._is_loaded and self.ocr is not None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics"""
        return {
            "loaded": self._is_loaded,
            "inferences": self._total_inferences,
            "avg_time_ms": round(
                self._total_processing_time / max(1, self._total_inferences), 2
            ),
            "cache_size": len(self._cache),
            "errors": self._errors,
        }
    
    def reset(self) -> None:
        """Reset statistics and cache"""
        self._total_inferences = 0
        self._total_processing_time = 0.0
        self._errors = 0
        self._cache.clear()
        self._log.info("PaddleOCR service reset")