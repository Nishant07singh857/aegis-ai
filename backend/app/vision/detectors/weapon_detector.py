# backend/app/vision/detectors/weapon_detector.py
"""
AEGIS AI - Weapon Detector
Identifies weapons being used by players in spectator view.
Detects weapon type from HUD and kill feed.
"""

import time
from typing import List, Optional
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.weapon")


@dataclass
class WeaponInfo:
    """Weapon detection information"""
    player_id: str = ""
    player_name: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Current weapons
    primary_weapon: str = ""
    secondary_weapon: str = ""
    pistol: str = ""
    melee: str = ""
    
    # Ammo
    current_ammo: int = 0
    max_ammo: int = 0
    reserve_ammo: int = 0
    is_reloading: bool = False
    
    # Attachments (detected from weapon icon)
    attachments: List[str] = field(default_factory=list)
    
    # Weapon category
    weapon_category: str = ""  # AR, SMG, Sniper, DMR, Shotgun, LMG, Pistol
    
    # Position on HUD
    weapon_display_x: float = 0.0
    weapon_display_y: float = 0.0
    
    # Detection confidence
    confidence: float = 0.0


class WeaponDetector(BaseDetector):
    """
    Identifies weapons from the BGMI spectator HUD.
    
    Detects:
    - Primary and secondary weapons
    - Current ammo count
    - Reload state
    - Weapon attachments
    - Weapon category
    
    Uses weapon icon template matching and OCR for ammo.
    """
    
    def __init__(self):
        super().__init__(name="weapon_detector")
        
        # Weapon display region (bottom-center HUD)
        self.weapon_region = DetectionBox(
            x=0.40, y=0.85, width=0.20, height=0.10
        )
        
        # Weapon database
        self.weapon_database = {
            "M416": {"category": "AR", "ammo_type": "5.56mm", "max_ammo": 30},
            "AKM": {"category": "AR", "ammo_type": "7.62mm", "max_ammo": 30},
            "SCAR-L": {"category": "AR", "ammo_type": "5.56mm", "max_ammo": 30},
            "GROZA": {"category": "AR", "ammo_type": "7.62mm", "max_ammo": 30},
            "AUG": {"category": "AR", "ammo_type": "5.56mm", "max_ammo": 30},
            "M762": {"category": "AR", "ammo_type": "7.62mm", "max_ammo": 30},
            "UMP45": {"category": "SMG", "ammo_type": ".45ACP", "max_ammo": 30},
            "Vector": {"category": "SMG", "ammo_type": ".45ACP", "max_ammo": 19},
            "UZI": {"category": "SMG", "ammo_type": "9mm", "max_ammo": 25},
            "AWM": {"category": "Sniper", "ammo_type": ".300", "max_ammo": 5},
            "M24": {"category": "Sniper", "ammo_type": "7.62mm", "max_ammo": 5},
            "Kar98k": {"category": "Sniper", "ammo_type": "7.62mm", "max_ammo": 5},
            "Mini14": {"category": "DMR", "ammo_type": "5.56mm", "max_ammo": 20},
            "SKS": {"category": "DMR", "ammo_type": "7.62mm", "max_ammo": 10},
            "Mk14": {"category": "DMR", "ammo_type": "7.62mm", "max_ammo": 10},
            "S12K": {"category": "Shotgun", "ammo_type": "12 Gauge", "max_ammo": 5},
            "DBS": {"category": "Shotgun", "ammo_type": "12 Gauge", "max_ammo": 14},
            "DP-28": {"category": "LMG", "ammo_type": "7.62mm", "max_ammo": 47},
            "M249": {"category": "LMG", "ammo_type": "5.56mm", "max_ammo": 75},
        }
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect weapons from frame.
        
        Args:
            frame: Full game frame
            **kwargs: player_id
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Extract weapon region
        x1 = int(self.weapon_region.x * w)
        y1 = int(self.weapon_region.y * h)
        x2 = int((self.weapon_region.x + self.weapon_region.width) * w)
        y2 = int((self.weapon_region.y + self.weapon_region.height) * h)
        
        region = frame[y1:y2, x1:x2]
        
        if region.size == 0:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w,
                frame_height=h,
            )
        
        # Detect weapon
        weapon_info = self._detect_weapon(region, x1, y1)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if weapon_info else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "weapon": {
                    "primary": weapon_info.primary_weapon if weapon_info else "",
                    "secondary": weapon_info.secondary_weapon if weapon_info else "",
                    "ammo": weapon_info.current_ammo if weapon_info else 0,
                    "max_ammo": weapon_info.max_ammo if weapon_info else 0,
                    "category": weapon_info.weapon_category if weapon_info else "",
                    "reloading": weapon_info.is_reloading if weapon_info else False,
                    "confidence": round(weapon_info.confidence, 3) if weapon_info else 0,
                },
            },
        )
        
        self._last_result = result
        return result
    
    def _detect_weapon(
        self,
        region: np.ndarray,
        offset_x: int,
        offset_y: int,
    ) -> Optional[WeaponInfo]:
        """Detect weapon name and ammo"""
        
        info = WeaponInfo(
            weapon_display_x=offset_x,
            weapon_display_y=offset_y,
        )
        
        try:
            import cv2
            
            # Try OCR for weapon name
            from paddleocr import PaddleOCR
            
            if not hasattr(self, '_ocr'):
                self._ocr = PaddleOCR(
                    use_angle_cls=False,
                    lang='en',
                    use_gpu=True,
                    show_log=False,
                )
            
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            _, thresh = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
            
            results = self._ocr.ocr(thresh, cls=False)
            
            if results and results[0]:
                texts = []
                for line in results[0]:
                    if line and len(line) >= 2:
                        texts.append(line[1][0])
                
                # Find weapon name
                for text in texts:
                    text_upper = text.upper().strip()
                    for weapon_name in self.weapon_database:
                        if weapon_name.upper() in text_upper:
                            if not info.primary_weapon:
                                info.primary_weapon = weapon_name
                                info.weapon_category = self.weapon_database[weapon_name]["category"]
                                info.max_ammo = self.weapon_database[weapon_name]["max_ammo"]
                            elif not info.secondary_weapon:
                                info.secondary_weapon = weapon_name
                            break
                
                # Find ammo count
                import re
                for text in texts:
                    numbers = re.findall(r'\d+', text)
                    if numbers:
                        ammo = int(numbers[0])
                        if ammo <= info.max_ammo:
                            info.current_ammo = ammo
                            break
                
                info.confidence = 0.7 if info.primary_weapon else 0.3
            
        except ImportError:
            self._log.warning("PaddleOCR not available")
        except Exception as e:
            self._log.error(f"Weapon detection error: {e}")
        
        return info if info.primary_weapon else None
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get weapon detection region"""
        return [self.weapon_region]