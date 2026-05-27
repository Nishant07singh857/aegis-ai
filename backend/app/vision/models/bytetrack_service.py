# backend/app/vision/models/bytetrack_service.py
"""
AEGIS AI - ByteTrack Service
Production wrapper for ByteTrack multi-object tracking.
Tracks players, vehicles, and objects across video frames.
"""

import time
import threading
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.bytetrack")


@dataclass
class TrackConfig:
    """ByteTrack configuration"""
    # Detection thresholds
    track_high_thresh: float = 0.5
    track_low_thresh: float = 0.1
    new_track_thresh: float = 0.6
    
    # Matching
    track_buffer: int = 30  # Frames to keep lost tracks
    match_thresh: float = 0.8
    
    # Kalman filter
    kalman_format: str = "default"
    
    # Performance
    fps: int = 30
    use_gpu: bool = True
    
    # Output
    min_track_length: int = 3  # Minimum frames to confirm track
    max_lost_frames: int = 30   # Frames before removing lost track


@dataclass
class TrackedObject:
    """A tracked object"""
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    
    # Current state
    bbox: Tuple[float, float, float, float]  # (x, y, w, h)
    center: Tuple[float, float]
    velocity: Tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    
    # Tracking
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    frames_tracked: int = 0
    frames_lost: int = 0
    is_active: bool = True
    
    # History
    position_history: List[Tuple[float, float, float]] = field(default_factory=list)
    
    def predict_position(self, frames_ahead: int = 1, fps: int = 30) -> Tuple[float, float]:
        """Predict future position based on velocity"""
        dt = frames_ahead / fps
        pred_x = self.center[0] + self.velocity[0] * dt
        pred_y = self.center[1] + self.velocity[1] * dt
        return (pred_x, pred_y)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "track_id": self.track_id,
            "class_name": self.class_name,
            "confidence": round(self.confidence, 3),
            "bbox": [round(x, 1) for x in self.bbox],
            "center": [round(self.center[0], 1), round(self.center[1], 1)],
            "speed": round(self.speed, 1),
            "frames_tracked": self.frames_tracked,
            "is_active": self.is_active,
        }


@dataclass
class TrackResult:
    """Complete tracking result"""
    tracked_objects: List[TrackedObject] = field(default_factory=list)
    active_tracks: int = 0
    total_tracks: int = 0
    processing_time_ms: float = 0.0
    timestamp: float = field(default_factory=time.time)
    
    def get_by_id(self, track_id: int) -> Optional[TrackedObject]:
        """Get tracked object by ID"""
        for obj in self.tracked_objects:
            if obj.track_id == track_id:
                return obj
        return None
    
    def get_active(self) -> List[TrackedObject]:
        """Get active tracked objects"""
        return [obj for obj in self.tracked_objects if obj.is_active]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "active_tracks": self.active_tracks,
            "total_tracks": self.total_tracks,
            "objects": [obj.to_dict() for obj in self.tracked_objects],
            "processing_time_ms": round(self.processing_time_ms, 2),
        }


class ByteTrackService:
    """
    Production ByteTrack service for multi-object tracking.
    
    Features:
    - Multi-class tracking
    - Velocity and speed estimation
    - Position prediction
    - Track management (create, update, delete)
    - Lost track recovery
    - Performance optimized
    """
    
    def __init__(self, config: Optional[TrackConfig] = None):
        self.config = config or TrackConfig()
        self.tracker: Optional[Any] = None
        
        # Track storage
        self.tracks: Dict[int, TrackedObject] = {}
        self._next_track_id = 1
        
        # Thread safety
        self._lock = threading.Lock()
        self._is_initialized = False
        
        # Statistics
        self._total_frames = 0
        self._total_tracks_created = 0
        self._total_processing_time = 0.0
        
        self._log = logger.bind(component="bytetrack_service")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def initialize(self, frame_rate: Optional[int] = None) -> bool:
        """Initialize ByteTrack tracker"""
        
        try:
            from boxmot import ByteTrack
            
            with self._lock:
                fps = frame_rate or self.config.fps
                
                self.tracker = ByteTrack(
                    track_high_thresh=self.config.track_high_thresh,
                    track_low_thresh=self.config.track_low_thresh,
                    new_track_thresh=self.config.new_track_thresh,
                    track_buffer=self.config.track_buffer,
                    match_thresh=self.config.match_thresh,
                    frame_rate=fps,
                )
                
                self._is_initialized = True
                
                self._log.info(f"ByteTrack initialized at {fps} FPS")
                return True
                
        except ImportError:
            self._log.warning("boxmot not installed - using simple tracker")
            self._is_initialized = True
            return True
        except Exception as e:
            self._log.error(f"Failed to initialize ByteTrack: {e}")
            return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update(
        self,
        detections: List[Dict[str, Any]],
        frame_shape: Tuple[int, int] = (1920, 1080),
    ) -> TrackResult:
        """
        Update tracker with new detections.
        
        Args:
            detections: List of detection dicts with 'bbox', 'confidence', 'class_id'
            frame_shape: (width, height) of frame
            
        Returns:
            TrackResult with tracked objects
        """
        
        start_time = time.time()
        self._total_frames += 1
        
        with self._lock:
            # Format detections for ByteTrack
            if self._is_initialized and self.tracker is not None:
                try:
                    dets = self._format_detections(detections, frame_shape)
                    
                    if len(dets) > 0:
                        tracks = self.tracker.update(dets, frame_shape)
                    else:
                        tracks = np.empty((0, 5))
                    
                    # Update tracks
                    tracked = self._update_tracks(tracks, detections, frame_shape)
                    
                except Exception as e:
                    self._log.error(f"Tracking error: {e}")
                    tracked = self._simple_track(detections, frame_shape)
            else:
                tracked = self._simple_track(detections, frame_shape)
        
        processing_time = (time.time() - start_time) * 1000
        self._total_processing_time += processing_time
        
        return TrackResult(
            tracked_objects=tracked,
            active_tracks=len([t for t in tracked if t.is_active]),
            total_tracks=len(self.tracks),
            processing_time_ms=processing_time,
        )
    
    def _format_detections(
        self,
        detections: List[Dict[str, Any]],
        frame_shape: Tuple[int, int],
    ) -> np.ndarray:
        """Format detections for ByteTrack input"""
        
        img_w, img_h = frame_shape
        
        formatted = []
        for det in detections:
            bbox = det.get("bbox", det.get("bbox_pixels", [0, 0, 0, 0]))
            conf = det.get("confidence", det.get("conf", 0.5))
            cls_id = det.get("class_id", 0)
            
            # Convert to (x1, y1, x2, y2) in pixels
            if len(bbox) == 4:
                x, y, w, h = bbox
                x1 = x
                y1 = y
                x2 = x + w
                y2 = y + h
                
                formatted.append([x1, y1, x2, y2, conf, cls_id])
        
        return np.array(formatted) if formatted else np.empty((0, 6))
    
    def _update_tracks(
        self,
        tracker_output: np.ndarray,
        detections: List[Dict[str, Any]],
        frame_shape: Tuple[int, int],
    ) -> List[TrackedObject]:
        """Update tracked objects from tracker output"""
        
        tracked = []
        current_time = time.time()
        active_ids = set()
        
        img_w, img_h = frame_shape
        
        for track_data in tracker_output:
            if len(track_data) >= 5:
                x1, y1, x2, y2, track_id = track_data[:5]
                track_id = int(track_id)
                
                # Find matching detection for class info
                cls_id = 0
                cls_name = "unknown"
                conf = 0.5
                
                for det in detections:
                    det_bbox = det.get("bbox_pixels", [0, 0, 0, 0])
                    if len(det_bbox) == 4:
                        dx, dy, dw, dh = det_bbox
                        # Simple IOU check
                        if abs(x1 - dx) < 20 and abs(y1 - dy) < 20:
                            cls_id = det.get("class_id", 0)
                            cls_name = det.get("class_name", "unknown")
                            conf = det.get("confidence", 0.5)
                            break
                
                # Create or update track
                if track_id in self.tracks:
                    obj = self.tracks[track_id]
                else:
                    obj = TrackedObject(
                        track_id=track_id,
                        class_id=cls_id,
                        class_name=cls_name,
                        confidence=conf,
                        bbox=(x1, y1, x2 - x1, y2 - y1),
                        center=((x1 + x2) / 2, (y1 + y2) / 2),
                    )
                    self.tracks[track_id] = obj
                    self._total_tracks_created += 1
                
                # Update state
                old_center = obj.center
                new_center = ((x1 + x2) / 2, (y1 + y2) / 2)
                
                # Calculate velocity
                dt = max(0.001, current_time - obj.last_seen)
                obj.velocity = (
                    (new_center[0] - old_center[0]) / dt,
                    (new_center[1] - old_center[1]) / dt,
                )
                obj.speed = np.sqrt(obj.velocity[0]**2 + obj.velocity[1]**2)
                
                # Update bbox and center
                obj.bbox = (x1, y1, x2 - x1, y2 - y1)
                obj.center = new_center
                obj.confidence = conf
                obj.last_seen = current_time
                obj.frames_tracked += 1
                obj.frames_lost = 0
                obj.is_active = True
                
                # Store position history
                obj.position_history.append((new_center[0], new_center[1], current_time))
                if len(obj.position_history) > 100:
                    obj.position_history = obj.position_history[-100:]
                
                active_ids.add(track_id)
                tracked.append(obj)
        
        # Mark inactive tracks
        for track_id, obj in self.tracks.items():
            if track_id not in active_ids:
                obj.frames_lost += 1
                obj.is_active = obj.frames_lost < self.config.max_lost_frames
        
        # Remove very old lost tracks
        lost_tracks = [
            tid for tid, obj in self.tracks.items()
            if obj.frames_lost > self.config.max_lost_frames * 2
        ]
        for tid in lost_tracks:
            del self.tracks[tid]
        
        return tracked
    
    def _simple_track(
        self,
        detections: List[Dict[str, Any]],
        frame_shape: Tuple[int, int],
    ) -> List[TrackedObject]:
        """Simple IOU-based tracking fallback"""
        
        tracked = []
        current_time = time.time()
        
        for det in detections:
            bbox = det.get("bbox_pixels", det.get("bbox", [0, 0, 0, 0]))
            if len(bbox) != 4:
                continue
            
            x, y, w, h = bbox
            center = (x + w / 2, y + h / 2)
            
            # Find best matching existing track
            best_match = None
            best_dist = float('inf')
            
            for track_id, obj in self.tracks.items():
                if not obj.is_active:
                    continue
                
                dist = np.sqrt(
                    (center[0] - obj.center[0])**2 +
                    (center[1] - obj.center[1])**2
                )
                
                if dist < best_dist and dist < 100:
                    best_dist = dist
                    best_match = track_id
            
            if best_match is not None:
                obj = self.tracks[best_match]
                
                # Update velocity
                dt = max(0.001, current_time - obj.last_seen)
                obj.velocity = (
                    (center[0] - obj.center[0]) / dt,
                    (center[1] - obj.center[1]) / dt,
                )
                obj.speed = np.sqrt(obj.velocity[0]**2 + obj.velocity[1]**2)
                
                obj.bbox = (x, y, w, h)
                obj.center = center
                obj.last_seen = current_time
                obj.frames_tracked += 1
                obj.frames_lost = 0
                obj.is_active = True
                
                tracked.append(obj)
            else:
                # New track
                new_id = self._next_track_id
                self._next_track_id += 1
                
                obj = TrackedObject(
                    track_id=new_id,
                    class_id=det.get("class_id", 0),
                    class_name=det.get("class_name", "unknown"),
                    confidence=det.get("confidence", 0.5),
                    bbox=(x, y, w, h),
                    center=center,
                )
                
                self.tracks[new_id] = obj
                self._total_tracks_created += 1
                tracked.append(obj)
        
        return tracked
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_track(self, track_id: int) -> Optional[TrackedObject]:
        """Get a specific track"""
        return self.tracks.get(track_id)
    
    def get_active_tracks(self) -> List[TrackedObject]:
        """Get all active tracks"""
        return [obj for obj in self.tracks.values() if obj.is_active]
    
    def remove_track(self, track_id: int) -> bool:
        """Remove a track"""
        if track_id in self.tracks:
            del self.tracks[track_id]
            return True
        return False
    
    def clear_tracks(self) -> None:
        """Clear all tracks"""
        self.tracks.clear()
        self._next_track_id = 1
    
    def is_initialized(self) -> bool:
        """Check if tracker is initialized"""
        return self._is_initialized
    
    def get_stats(self) -> Dict[str, Any]:
        """Get tracking statistics"""
        return {
            "initialized": self._is_initialized,
            "frames_processed": self._total_frames,
            "active_tracks": len(self.get_active_tracks()),
            "total_tracks": len(self.tracks),
            "tracks_created": self._total_tracks_created,
            "avg_time_ms": round(
                self._total_processing_time / max(1, self._total_frames), 2
            ),
        }
    
    def reset(self) -> None:
        """Reset tracker"""
        self.tracks.clear()
        self._next_track_id = 1
        self._total_frames = 0
        self._total_tracks_created = 0
        self._total_processing_time = 0.0
        self._log.info("ByteTrack service reset")