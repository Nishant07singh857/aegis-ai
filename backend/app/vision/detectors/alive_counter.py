# backend/app/vision/detectors/alive_counter.py
"""
AEGIS AI - Alive Counter
Detects the number of alive players/teams from the BGMI HUD.
Reads the alive counter display in spectator mode.
"""

import time
from typing import List, Optional, Tuple
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.alive_counter")


@dataclass
class AliveCountInfo:
    """Alive count information"""
    timestamp: float = field(default_factory=time.time)
    
    # Counts
    total_players_alive: int = 64
    total_teams_alive: int = 16
    players_remaining: int = 64
    teams_remaining: int = 16
    
    # Changes
    players_changed: bool = False
    teams_changed: bool = False
    last_players_count: int = 64
    last_teams_count: int = 16
    
    # Display location
    display_x: float = 0.0
    display_y: float = 0.0
    
    # Detection confidence
    confidence: float = 0.0


class AliveCounter(BaseDetector):
    """
    Reads the alive player/team counter from the BGMI HUD.
    
    The counter shows "ALIVE: X" or "REMAINING: X" 
    in the top-right area of the screen in spectator mode.
    
    Uses OCR to read the number and tracks changes over time.
    """
    
    def __init__(self):
        super().__init__(name="alive_counter")
        
        # Counter display region (top-right area)
        self.counter_region = DetectionBox(
            x=0.85, y=0.02, width=0.12, height=0.06
        )
        
        # Track previous counts for change detection
        self._previous_players = 64
        self._previous_teams = 16
        self._count_history: List[Tuple[int, int, float]] = []
        
        # Known total players per map
        self.map_totals = {
            "erangel": 64,
            "miramar": 64,
            "sanhok": 64,
            "vikendi": 64,
            "livik": 52,
            "karakin": 64,
        }
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Read alive counter from frame.
        
        Args:
            frame: Full game frame
            **kwargs: map_name, expected_players
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Extract counter region
        x1 = int(self.counter_region.x * w)
        y1 = int(self.counter_region.y * h)
        x2 = int((self.counter_region.x + self.counter_region.width) * w)
        y2 = int((self.counter_region.y + self.counter_region.height) * h)
        
        counter_region = frame[y1:y2, x1:x2]
        
        if counter_region.size == 0:
            return DetectionResult(
                detector_name=self.name,
                status=DetectionStatus.FAILED,
                frame_width=w,
                frame_height=h,
            )
        
        # Read counter using OCR
        info = self._read_counter(counter_region, x1, y1)
        
        if info:
            # Check for changes
            if info.players_remaining != self._previous_players:
                info.players_changed = True
                info.last_players_count = self._previous_players
                self._previous_players = info.players_remaining
            
            if info.teams_remaining != self._previous_teams:
                info.teams_changed = True
                info.last_teams_count = self._previous_teams
                self._previous_teams = info.teams_remaining
            
            # Track history
            self._count_history.append(
                (info.players_remaining, info.teams_remaining, time.time())
            )
            
            # Keep last 100 entries
            if len(self._count_history) > 100:
                self._count_history = self._count_history[-100:]
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if info else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "alive_count": {
                    "players": info.players_remaining if info else 0,
                    "teams": info.teams_remaining if info else 0,
                    "players_changed": info.players_changed if info else False,
                    "teams_changed": info.teams_changed if info else False,
                    "confidence": round(info.confidence, 3) if info else 0,
                },
            },
        )
        
        self._last_result = result
        return result
    
    def _read_counter(
        self,
        region: np.ndarray,
        offset_x: int,
        offset_y: int,
    ) -> Optional[AliveCountInfo]:
        """Read alive counter using OCR"""
        
        try:
            import cv2
            
            # Convert to grayscale
            gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
            
            # Threshold to get white text
            _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
            
            # Use OCR to read number
            from paddleocr import PaddleOCR
            
            if not hasattr(self, '_ocr'):
                self._ocr = PaddleOCR(
                    use_angle_cls=False,
                    lang='en',
                    use_gpu=True,
                    show_log=False,
                )
            
            results = self._ocr.ocr(thresh, cls=False)
            
            if results and results[0]:
                numbers = []
                for line in results[0]:
                    if line and len(line) >= 2:
                        text = line[1][0]
                        # Extract numbers
                        import re
                        found_numbers = re.findall(r'\d+', text)
                        numbers.extend([int(n) for n in found_numbers])
                
                if numbers:
                    # First number is usually players alive
                    players = numbers[0] if numbers else 64
                    # Second number might be teams
                    teams = numbers[1] if len(numbers) > 1 else max(1, players // 4)
                    
                    return AliveCountInfo(
                        players_remaining=players,
                        teams_remaining=teams,
                        display_x=offset_x,
                        display_y=offset_y,
                        confidence=0.8,
                    )
            
        except ImportError:
            self._log.warning("PaddleOCR not available")
        except Exception as e:
            self._log.error(f"Counter OCR error: {e}")
        
        return None
    
    def get_players_remaining(self) -> int:
        """Get current players remaining"""
        return self._previous_players
    
    def get_teams_remaining(self) -> int:
        """Get current teams remaining"""
        return self._previous_teams
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get counter detection region"""
        return [self.counter_region]