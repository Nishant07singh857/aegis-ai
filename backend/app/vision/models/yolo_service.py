# backend/app/vision/models/yolo_service.py
"""
AEGIS AI - YOLOv8 Service
Production wrapper for YOLOv8 object detection.
Supports multiple model sizes, TensorRT optimization,
dynamic input sizes, and batch processing.
"""

import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.yolo")


class YOLOMode(str, Enum):
    """YOLO inference modes"""
    DETECT = "detect"
    TRACK = "track"
    POSE = "pose"
    SEGMENT = "segment"
    CLASSIFY = "classify"


class YOLOSize(str, Enum):
    """YOLO model sizes"""
    NANO = "n"       # Fastest, lowest accuracy
    SMALL = "s"      # Fast, decent accuracy
    MEDIUM = "m"     # Balanced
    LARGE = "l"      # High accuracy
    XLARGE = "x"     # Best accuracy, slowest


@dataclass
class YOLOConfig:
    """YOLO model configuration"""
    model_size: YOLOSize = YOLOSize.MEDIUM
    mode: YOLOMode = YOLOMode.DETECT
    input_size: Tuple[int, int] = (640, 640)
    
    # Inference
    confidence_threshold: float = 0.5
    iou_threshold: float = 0.45
    max_detections: int = 300
    half_precision: bool = True  # FP16
    int8_quantization: bool = False
    
    # Performance
    batch_size: int = 1
    num_workers: int = 2
    warmup_iterations: int = 3
    
    # Model paths
    model_path: str = ""
    engine_path: str = ""  # TensorRT engine
    
    # Classes
    classes: Optional[List[str]] = None
    filter_classes: Optional[List[int]] = None
    
    # Augmentation
    augment: bool = False
    agnostic_nms: bool = False
    
    # Device
    device: str = "cuda:0"
    use_tensorrt: bool = False
    
    # Tracking (for track mode)
    tracker_config: str = "bytetrack.yaml"


@dataclass
class YOLODetection:
    """Single YOLO detection result"""
    class_id: int
    class_name: str
    confidence: float
    bbox: Tuple[float, float, float, float]  # (x, y, w, h) normalized 0-1
    bbox_pixels: Tuple[int, int, int, int]    # (x, y, w, h) in pixels
    track_id: Optional[int] = None
    
    @property
    def center(self) -> Tuple[float, float]:
        return (self.bbox[0] + self.bbox[2] / 2, self.bbox[1] + self.bbox[3] / 2)
    
    @property
    def area(self) -> float:
        return self.bbox[2] * self.bbox[3]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "class_id": self.class_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 3),
            "bbox": [round(x, 4) for x in self.bbox],
            "center": [round(self.center[0], 4), round(self.center[1], 4)],
            "track_id": self.track_id,
        }


@dataclass
class YOLOResult:
    """Complete YOLO inference result"""
    detections: List[YOLODetection] = field(default_factory=list)
    image_shape: Tuple[int, int] = (640, 640)
    processing_time_ms: float = 0.0
    preprocessing_time_ms: float = 0.0
    inference_time_ms: float = 0.0
    postprocessing_time_ms: float = 0.0
    model_name: str = ""
    timestamp: float = field(default_factory=time.time)
    
    @property
    def count(self) -> int:
        return len(self.detections)
    
    def get_by_class(self, class_name: str) -> List[YOLODetection]:
        """Get detections of a specific class"""
        return [d for d in self.detections if d.class_name == class_name]
    
    def get_by_confidence(self, threshold: float) -> List[YOLODetection]:
        """Get detections above confidence threshold"""
        return [d for d in self.detections if d.confidence >= threshold]
    
    def get_best(self, class_name: Optional[str] = None) -> Optional[YOLODetection]:
        """Get highest confidence detection"""
        candidates = self.detections
        if class_name:
            candidates = self.get_by_class(class_name)
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.confidence)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "detections": [d.to_dict() for d in self.detections],
            "timing": {
                "total_ms": round(self.processing_time_ms, 2),
                "preprocess_ms": round(self.preprocessing_time_ms, 2),
                "inference_ms": round(self.inference_time_ms, 2),
                "postprocess_ms": round(self.postprocessing_time_ms, 2),
            },
            "model": self.model_name,
        }


class YOLOService:
    """
    Production YOLOv8 service with advanced features.
    
    Features:
    - Multiple model sizes (n, s, m, l, x)
    - TensorRT acceleration
    - FP16/INT8 optimization
    - Batch inference
    - Dynamic input sizing
    - Class filtering
    - Model warmup
    - Performance monitoring
    - Thread-safe inference
    """
    
    def __init__(self, config: Optional[YOLOConfig] = None):
        self.config = config or YOLOConfig()
        self.model: Optional[Any] = None
        self.model_name: str = ""
        
        # Thread safety
        self._lock = threading.Lock()
        self._is_loaded = False
        
        # Statistics
        self._total_inferences = 0
        self._total_detections = 0
        self._total_processing_time = 0.0
        self._errors = 0
        
        # Class names (COCO default)
        self.default_classes = [
            "person", "bicycle", "car", "motorcycle", "airplane", "bus",
            "train", "truck", "boat", "traffic light", "fire hydrant",
            "stop sign", "parking meter", "bench", "bird", "cat", "dog",
            "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe",
            "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
            "skis", "snowboard", "sports ball", "kite", "baseball bat",
            "baseball glove", "skateboard", "surfboard", "tennis racket",
            "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl",
            "banana", "apple", "sandwich", "orange", "broccoli", "carrot",
            "hot dog", "pizza", "donut", "cake", "chair", "couch",
            "potted plant", "bed", "dining table", "toilet", "tv", "laptop",
            "mouse", "remote", "keyboard", "cell phone", "microwave",
            "oven", "toaster", "sink", "refrigerator", "book", "clock",
            "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
        ]
        
        self._log = logger.bind(component="yolo_service")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_model(self, model_path: Optional[str] = None) -> bool:
        """
        Load YOLO model.
        
        Args:
            model_path: Path to model weights. If None, uses config path.
            
        Returns:
            True if loaded successfully
        """
        
        path = model_path or self.config.model_path
        
        if not path:
            # Auto-select model based on size
            path = f"yolov8{self.config.model_size.value}.pt"
        
        try:
            from ultralytics import YOLO
            
            with self._lock:
                self._log.info(f"Loading YOLO model: {path}")
                
                # Load model
                self.model = YOLO(path)
                
                # Move to device
                if self.config.device:
                    self.model.to(self.config.device)
                
                # Apply optimizations
                if self.config.half_precision:
                    self.model.model.half()
                
                self.model_name = f"yolov8{self.config.model_size.value}"
                self._is_loaded = True
                
                # Warmup
                self._warmup()
                
                self._log.info(
                    f"YOLO model loaded: {self.model_name} "
                    f"on {self.config.device}"
                )
                
                return True
                
        except ImportError:
            self._log.error("Ultralytics not installed")
            return False
        except Exception as e:
            self._log.error(f"Failed to load YOLO model: {e}")
            self._errors += 1
            return False
    
    def load_tensorrt(self, engine_path: str) -> bool:
        """Load TensorRT optimized model"""
        
        try:
            from ultralytics import YOLO
            
            with self._lock:
                self._log.info(f"Loading TensorRT engine: {engine_path}")
                
                self.model = YOLO(engine_path)
                self.model_name = f"yolov8{self.config.model_size.value}-trt"
                self._is_loaded = True
                self.config.use_tensorrt = True
                
                self._warmup()
                
                return True
                
        except Exception as e:
            self._log.error(f"Failed to load TensorRT model: {e}")
            return False
    
    def export_tensorrt(self, output_path: str, fp16: bool = True) -> bool:
        """Export model to TensorRT engine"""
        
        if not self._is_loaded:
            return False
        
        try:
            with self._lock:
                self._log.info(f"Exporting to TensorRT: {output_path}")
                
                self.model.export(
                    format="engine",
                    half=fp16,
                    imgsz=self.config.input_size[0],
                )
                
                return True
                
        except Exception as e:
            self._log.error(f"TensorRT export failed: {e}")
            return False
    
    def _warmup(self) -> None:
        """Warm up model with dummy inference"""
        
        if not self._is_loaded or self.model is None:
            return
        
        try:
            dummy = np.zeros(
                (self.config.input_size[1], self.config.input_size[0], 3),
                dtype=np.uint8,
            )
            
            for _ in range(self.config.warmup_iterations):
                self.model(dummy, verbose=False)
            
            self._log.debug(f"Model warmed up ({self.config.warmup_iterations} iterations)")
            
        except Exception as e:
            self._log.warning(f"Warmup failed: {e}")
    
    def unload_model(self) -> None:
        """Unload model and free memory"""
        
        with self._lock:
            self.model = None
            self._is_loaded = False
            
            # Clear CUDA cache
            try:
                import torch
                torch.cuda.empty_cache()
            except ImportError:
                pass
            
            self._log.info("YOLO model unloaded")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INFERENCE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect(
        self,
        image: np.ndarray,
        confidence_threshold: Optional[float] = None,
        iou_threshold: Optional[float] = None,
        filter_classes: Optional[List[int]] = None,
    ) -> YOLOResult:
        """
        Run object detection on an image.
        
        Args:
            image: Input image (BGR format)
            confidence_threshold: Override confidence threshold
            iou_threshold: Override IOU threshold
            filter_classes: Filter specific classes
            
        Returns:
            YOLOResult with detections
        """
        
        if not self._is_loaded or self.model is None:
            self._log.error("Model not loaded")
            return YOLOResult(model_name=self.model_name)
        
        start_time = time.time()
        conf = confidence_threshold or self.config.confidence_threshold
        iou = iou_threshold or self.config.iou_threshold
        classes = filter_classes or self.config.filter_classes
        
        try:
            with self._lock:
                # Run inference
                results = self.model(
                    image,
                    conf=conf,
                    iou=iou,
                    classes=classes,
                    verbose=False,
                    agnostic_nms=self.config.agnostic_nms,
                    augment=self.config.augment,
                    max_det=self.config.max_detections,
                )
            
            # Extract timing
            inference_time = 0.0
            preprocess_time = 0.0
            postprocess_time = 0.0
            
            if hasattr(results[0], 'speed'):
                speed = results[0].speed
                preprocess_time = speed.get('preprocess', 0.0)
                inference_time = speed.get('inference', 0.0)
                postprocess_time = speed.get('postprocess', 0.0)
            
            # Parse detections
            detections = self._parse_results(results, image.shape)
            
            processing_time = (time.time() - start_time) * 1000
            
            # Update statistics
            self._total_inferences += 1
            self._total_detections += len(detections)
            self._total_processing_time += processing_time
            
            return YOLOResult(
                detections=detections,
                image_shape=(image.shape[1], image.shape[0]),
                processing_time_ms=processing_time,
                preprocessing_time_ms=preprocess_time,
                inference_time_ms=inference_time,
                postprocessing_time_ms=postprocess_time,
                model_name=self.model_name,
            )
            
        except Exception as e:
            self._log.error(f"Inference error: {e}")
            self._errors += 1
            
            return YOLOResult(
                image_shape=(image.shape[1], image.shape[0]) if image is not None else (0, 0),
                processing_time_ms=(time.time() - start_time) * 1000,
                model_name=self.model_name,
            )
    
    def detect_batch(
        self,
        images: List[np.ndarray],
        **kwargs,
    ) -> List[YOLOResult]:
        """Run detection on multiple images"""
        return [self.detect(img, **kwargs) for img in images]
    
    def track(
        self,
        image: np.ndarray,
        persist: bool = True,
        **kwargs,
    ) -> YOLOResult:
        """
        Run object tracking on an image.
        Uses ByteTrack integration in YOLOv8.
        """
        
        if not self._is_loaded or self.model is None:
            return YOLOResult(model_name=self.model_name)
        
        start_time = time.time()
        
        try:
            with self._lock:
                results = self.model.track(
                    image,
                    persist=persist,
                    conf=kwargs.get('confidence_threshold', self.config.confidence_threshold),
                    iou=kwargs.get('iou_threshold', self.config.iou_threshold),
                    verbose=False,
                )
            
            detections = self._parse_results(results, image.shape, include_track_id=True)
            
            return YOLOResult(
                detections=detections,
                image_shape=(image.shape[1], image.shape[0]),
                processing_time_ms=(time.time() - start_time) * 1000,
                model_name=self.model_name,
            )
            
        except Exception as e:
            self._log.error(f"Tracking error: {e}")
            self._errors += 1
            return YOLOResult(model_name=self.model_name)
    
    def _parse_results(
        self,
        results: List[Any],
        image_shape: Tuple[int, int, int],
        include_track_id: bool = False,
    ) -> List[YOLODetection]:
        """Parse YOLO results into standard format"""
        
        detections = []
        
        img_h, img_w = image_shape[:2]
        class_names = self.config.classes or self.default_classes
        
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            
            for i in range(len(boxes)):
                # Get box coordinates
                xywh = boxes.xywh[i].tolist() if hasattr(boxes, 'xywh') else [0, 0, 0, 0]
                xyxy = boxes.xyxy[i].tolist() if hasattr(boxes, 'xyxy') else [0, 0, 0, 0]
                
                # Normalize coordinates
                norm_x = xywh[0] / img_w
                norm_y = xywh[1] / img_h
                norm_w = xywh[2] / img_w
                norm_h = xywh[3] / img_h
                
                # Get class info
                cls_id = int(boxes.cls[i]) if hasattr(boxes, 'cls') else 0
                conf = float(boxes.conf[i]) if hasattr(boxes, 'conf') else 0.0
                
                # Get track ID if available
                track_id = None
                if include_track_id and hasattr(boxes, 'id') and boxes.id is not None:
                    track_id = int(boxes.id[i]) if i < len(boxes.id) else None
                
                class_name = class_names[cls_id] if cls_id < len(class_names) else f"class_{cls_id}"
                
                detection = YOLODetection(
                    class_id=cls_id,
                    class_name=class_name,
                    confidence=conf,
                    bbox=(norm_x, norm_y, norm_w, norm_h),
                    bbox_pixels=(int(xywh[0]), int(xywh[1]), int(xywh[2]), int(xywh[3])),
                    track_id=track_id,
                )
                
                detections.append(detection)
        
        return detections
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def is_loaded(self) -> bool:
        """Check if model is loaded"""
        return self._is_loaded and self.model is not None
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get model information"""
        return {
            "model_name": self.model_name,
            "size": self.config.model_size.value,
            "mode": self.config.mode.value,
            "input_size": self.config.input_size,
            "loaded": self._is_loaded,
            "device": self.config.device,
            "tensorrt": self.config.use_tensorrt,
            "fp16": self.config.half_precision,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics"""
        return {
            "model": self.get_model_info(),
            "inferences": self._total_inferences,
            "detections": self._total_detections,
            "avg_time_ms": round(
                self._total_processing_time / max(1, self._total_inferences), 2
            ),
            "errors": self._errors,
        }
    
    def reset(self) -> None:
        """Reset statistics"""
        self._total_inferences = 0
        self._total_detections = 0
        self._total_processing_time = 0.0
        self._errors = 0
        self._log.info("YOLO service statistics reset")