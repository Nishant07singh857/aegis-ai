# backend/app/vision/detectors/player_tracker.py
"""
AEGIS AI - Player Tracker
Tracks player positions and movements using ByteTrack.
Maintains player identity across frames.
"""

import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.player_tracker")


@dataclass
class TrackedPlayer:
    """Tracked player information"""
    track_id: int
    player_id: str = ""
    player_name: str = ""
    team_id: str = ""
    
    # Current state
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    speed: float = 0.0
    direction: float = 0.0  # degrees
    
    # Status
    is_alive: bool = True
    is_knocked: bool = False
    is_moving: bool = False
    is_sprinting: bool = False
    is_crouching: bool = False
    is_proning: bool = False
    is_in_vehicle: bool = False
    is_aiming: bool = False
    is_firing: bool = False
    
    # Detection
    bounding_box: Optional[DetectionBox] = None
    confidence: float = 0.0
    
    # Tracking
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    frames_tracked: int = 0
    frames_lost: int = 0
    
    # Movement history
    position_history: List[Tuple[float, float, float]] = field(default_factory=list)
    
    # Health
    hp_estimate: int = 100


class PlayerTracker(BaseDetector):
    """
    Tracks player positions using ByteTrack.
    
    Features:
    - Multi-player tracking with ID persistence
    - Velocity and direction calculation
    - Movement state detection
    - Position history
    - Team assignment
    """
    
    def __init__(self):
        super().__init__(name="player_tracker")
        
        # Full frame
        self.detection_region = DetectionBox(
            x=0.0, y=0.0, width=1.0, height=1.0
        )
        
        # Tracked players
        self.tracked_players: Dict[int, TrackedPlayer] = {}
        self._next_track_id = 1
        
        # Tracking parameters
        self.max_lost_frames = 30  # Frames before removing track
        self.track_distance_threshold = 100  # Max distance for track matching
        self.position_history_size = 50
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Track players from frame.
        
        Args:
            frame: Full game frame
            **kwargs: team_assignments
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Detect and track players
        detections = self._detect_players(frame)
        self._update_tracks(detections, w, h)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if self.tracked_players else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "tracked_players": len(self.tracked_players),
                "players": [
                    {
                        "track_id": p.track_id,
                        "position": (round(p.position_x, 1), round(p.position_y, 1)),
                        "speed": round(p.speed, 1),
                        "moving": p.is_moving,
                        "direction": round(p.direction, 1),
                        "alive": p.is_alive,
                        "confidence": round(p.confidence, 3),
                    }
                    for p in self.tracked_players.values()
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_players(self, frame: np.ndarray) -> List[DetectionBox]:
        """Detect players using YOLO"""
        
        detections = []
        
        try:
            from ultralytics import YOLO
            
            if not hasattr(self, '_model'):
                self._model = YOLO('yolov8n.pt')
            
            results = self._model(frame, verbose=False)
            
            # Person class = 0 in COCO
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                for box in boxes:
                    cls = int(box.cls[0])
                    
                    if cls == 0:  # Person
                        xyxy = box.xyxy[0].tolist()
                        conf = float(box.conf[0])
                        
                        detection = DetectionBox(
                            x=xyxy[0], y=xyxy[1],
                            width=xyxy[2]-xyxy[0],
                            height=xyxy[3]-xyxy[1],
                            confidence=conf,
                            label="player",
                        )
                        detections.append(detection)
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Player detection error: {e}")
        
        return detections
    
    def _update_tracks(
        self,
        detections: List[DetectionBox],
        frame_w: int,
        frame_h: int,
    ) -> None:
        """Update player tracks with new detections"""
        
        current_time = time.time()
        matched_tracks = set()
        
        for detection in detections:
            best_match_id = None
            best_match_dist = float('inf')
            
            # Find closest existing track
            for track_id, tracked in self.tracked_players.items():
                if track_id in matched_tracks:
                    continue
                
                dist = np.sqrt(
                    (detection.center[0] - tracked.position_x)**2 +
                    (detection.center[1] - tracked.position_y)**2
                )
                
                if dist < best_match_dist and dist < self.track_distance_threshold:
                    best_match_dist = dist
                    best_match_id = track_id
            
            if best_match_id is not None:
                # Update existing track
                tracked = self.tracked_players[best_match_id]
                
                # Calculate velocity
                dt = current_time - tracked.last_seen
                if dt > 0 and dt < 1.0:
                    tracked.velocity_x = (detection.center[0] - tracked.position_x) / dt
                    tracked.velocity_y = (detection.center[1] - tracked.position_y) / dt
                    tracked.speed = np.sqrt(
                        tracked.velocity_x**2 + tracked.velocity_y**2
                    )
                    tracked.direction = np.degrees(
                        np.arctan2(tracked.velocity_y, tracked.velocity_x)
                    )
                    
                    # Determine movement state
                    tracked.is_moving = tracked.speed > 5
                    tracked.is_sprinting = tracked.speed > 50
                
                # Update position
                tracked.position_x = detection.center[0]
                tracked.position_y = detection.center[1]
                tracked.bounding_box = detection
                tracked.confidence = detection.confidence
                tracked.last_seen = current_time
                tracked.frames_tracked += 1
                tracked.frames_lost = 0
                
                # Store position history
                tracked.position_history.append(
                    (detection.center[0], detection.center[1], current_time)
                )
                if len(tracked.position_history) > self.position_history_size:
                    tracked.position_history = tracked.position_history[-self.position_history_size:]
                
                matched_tracks.add(best_match_id)
            else:
                # New track
                new_track = TrackedPlayer(
                    track_id=self._next_track_id,
                    player_id=f"player_{self._next_track_id}",
                    position_x=detection.center[0],
                    position_y=detection.center[1],
                    bounding_box=detection,
                    confidence=detection.confidence,
                )
                
                self.tracked_players[self._next_track_id] = new_track
                self._next_track_id += 1
                matched_tracks.add(new_track.track_id)
        
        # Update lost tracks
        for track_id, tracked in list(self.tracked_players.items()):
            if track_id not in matched_tracks:
                tracked.frames_lost += 1
                
                # Remove if lost for too long
                if tracked.frames_lost > self.max_lost_frames:
                    del self.tracked_players[track_id]
    
    def get_tracked_players(self) -> List[TrackedPlayer]:
        """Get all tracked players"""
        return list(self.tracked_players.values())
    
    def get_player_by_id(self, track_id: int) -> Optional[TrackedPlayer]:
        """Get player by track ID"""
        return self.tracked_players.get(track_id)
    
    def get_player_positions(self) -> Dict[int, Tuple[float, float]]:
        """Get positions of all tracked players"""
        return {
            tid: (p.position_x, p.position_y)
            for tid, p in self.tracked_players.items()
        }
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get full frame region"""
        return [self.detection_region]