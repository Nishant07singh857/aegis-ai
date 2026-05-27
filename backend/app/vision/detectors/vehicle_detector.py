# backend/app/vision/detectors/vehicle_detector.py
"""
AEGIS AI - Vehicle Detector
Detects vehicle usage from BGMI spectator view.
Tracks vehicle types, positions, and player interactions.
"""

import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.vehicle")


@dataclass
class VehicleInfo:
    """Vehicle detection information"""
    vehicle_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Vehicle type
    vehicle_type: str = ""  # buggy, bike, car, truck, boat, etc.
    vehicle_name: str = ""
    
    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    velocity_x: float = 0.0
    velocity_y: float = 0.0
    speed: float = 0.0
    direction: float = 0.0  # degrees
    
    # Status
    is_moving: bool = False
    is_destroyed: bool = False
    hp: int = 100
    
    # Occupants
    driver_id: str = ""
    passengers: List[str] = field(default_factory=list)
    occupant_count: int = 0
    
    # Detection
    bounding_box: Optional[DetectionBox] = None
    confidence: float = 0.0
    tracking_id: int = 0


class VehicleDetector(BaseDetector):
    """
    Detects vehicles in BGMI spectator view.
    
    Tracks:
    - Vehicle type (buggy, bike, car, truck, boat)
    - Vehicle position and movement
    - Vehicle HP
    - Players entering/exiting vehicles
    - Vehicle destruction
    
    Uses YOLO for vehicle detection and tracking.
    """
    
    def __init__(self):
        super().__init__(name="vehicle_detector")
        
        # Full frame for vehicle detection
        self.detection_region = DetectionBox(
            x=0.0, y=0.0, width=1.0, height=1.0
        )
        
        # Vehicle types in BGMI
        self.vehicle_types = {
            "buggy": {"max_hp": 1000, "max_speed": 90, "seats": 2},
            "dacia": {"max_hp": 1500, "max_speed": 115, "seats": 4},
            "uaz": {"max_hp": 1800, "max_speed": 100, "seats": 4},
            "motorcycle": {"max_hp": 500, "max_speed": 130, "seats": 2},
            "bike_3seat": {"max_hp": 800, "max_speed": 110, "seats": 3},
            "pickup": {"max_hp": 2000, "max_speed": 95, "seats": 4},
            "boat": {"max_hp": 1200, "max_speed": 70, "seats": 4},
            "jetski": {"max_hp": 600, "max_speed": 85, "seats": 2},
            "brdm": {"max_hp": 3000, "max_speed": 80, "seats": 4},
        }
        
        # Active vehicles being tracked
        self.active_vehicles: Dict[int, VehicleInfo] = {}
        self._next_tracking_id = 1
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect vehicles from frame.
        
        Args:
            frame: Full game frame
            **kwargs: known_vehicles
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Detect vehicles
        vehicles = self._detect_vehicles(frame)
        
        # Update tracking
        self._update_tracking(vehicles)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if vehicles else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "vehicles_detected": len(vehicles),
                "active_vehicles": len(self.active_vehicles),
                "vehicles": [
                    {
                        "type": v.vehicle_type,
                        "name": v.vehicle_name,
                        "position": (round(v.position_x, 1), round(v.position_y, 1)),
                        "speed": round(v.speed, 1),
                        "hp": v.hp,
                        "occupants": v.occupant_count,
                        "moving": v.is_moving,
                        "confidence": round(v.confidence, 3),
                    }
                    for v in vehicles
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_vehicles(self, frame: np.ndarray) -> List[VehicleInfo]:
        """Detect vehicles using YOLO"""
        
        vehicles = []
        
        try:
            from ultralytics import YOLO
            
            # Lazy load model
            if not hasattr(self, '_model'):
                self._model = YOLO('yolov8n.pt')  # Use nano model for speed
            
            results = self._model(frame, verbose=False)
            
            vehicle_classes = [2, 3, 4, 5, 6, 7, 8]  # COCO vehicle classes
            
            for result in results:
                boxes = result.boxes
                if boxes is None:
                    continue
                
                for box in boxes:
                    cls = int(box.cls[0])
                    
                    if cls not in vehicle_classes:
                        continue
                    
                    xyxy = box.xyxy[0].tolist()
                    conf = float(box.conf[0])
                    
                    # Map COCO class to BGMI vehicle
                    vehicle_type = self._map_class_to_vehicle(cls)
                    
                    vehicle = VehicleInfo(
                        vehicle_id=f"veh_{int(time.time()*1000)}",
                        vehicle_type=vehicle_type,
                        vehicle_name=vehicle_type.replace("_", " ").title(),
                        position_x=(xyxy[0] + xyxy[2]) / 2,
                        position_y=(xyxy[1] + xyxy[3]) / 2,
                        bounding_box=DetectionBox(
                            x=xyxy[0], y=xyxy[1],
                            width=xyxy[2]-xyxy[0], height=xyxy[3]-xyxy[1],
                            confidence=conf,
                        ),
                        confidence=conf,
                    )
                    
                    vehicles.append(vehicle)
            
        except ImportError:
            self._log.warning("YOLO not available")
        except Exception as e:
            self._log.error(f"Vehicle detection error: {e}")
        
        return vehicles
    
    def _map_class_to_vehicle(self, coco_class: int) -> str:
        """Map COCO class to BGMI vehicle type"""
        mapping = {
            2: "dacia",      # car → Dacia
            3: "motorcycle", # motorcycle → Bike
            4: "buggy",      # airplane → Buggy (closest match)
            5: "boat",       # boat → Boat
            6: "pickup",     # bus → Pickup
            7: "uaz",        # truck → UAZ
            8: "brdm",       # train → BRDM
        }
        return mapping.get(coco_class, "unknown")
    
    def _update_tracking(self, detected: List[VehicleInfo]) -> None:
        """Update vehicle tracking with new detections"""
        
        current_time = time.time()
        
        # Simple tracking: match by proximity
        for new_vehicle in detected:
            matched = False
            
            for track_id, tracked in list(self.active_vehicles.items()):
                # Check if same vehicle (within 100px)
                dist = np.sqrt(
                    (new_vehicle.position_x - tracked.position_x)**2 +
                    (new_vehicle.position_y - tracked.position_y)**2
                )
                
                if dist < 100:
                    # Update tracked vehicle
                    tracked.position_x = new_vehicle.position_x
                    tracked.position_y = new_vehicle.position_y
                    tracked.timestamp = current_time
                    
                    # Calculate speed if moving
                    if dist > 5:
                        tracked.is_moving = True
                        tracked.speed = dist / max(0.1, current_time - tracked.timestamp)
                        tracked.direction = np.degrees(
                            np.arctan2(
                                new_vehicle.position_y - tracked.position_y,
                                new_vehicle.position_x - tracked.position_x,
                            )
                        )
                    
                    matched = True
                    break
            
            if not matched:
                # New vehicle
                new_vehicle.tracking_id = self._next_tracking_id
                self._next_tracking_id += 1
                self.active_vehicles[new_vehicle.tracking_id] = new_vehicle
        
        # Remove stale vehicles (not seen for 5 seconds)
        self.active_vehicles = {
            tid: v for tid, v in self.active_vehicles.items()
            if current_time - v.timestamp < 5.0
        }
    
    def get_active_vehicles(self) -> List[VehicleInfo]:
        """Get currently tracked vehicles"""
        return list(self.active_vehicles.values())
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get full frame region"""
        return [self.detection_region]