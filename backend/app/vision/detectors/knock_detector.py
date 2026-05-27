# backend/app/vision/detectors/knock_detector.py
"""
AEGIS AI - Knock Detector
Detects knock vs kill events from the BGMI kill feed and HUD.
Distinguishes between knockdowns and eliminations.
"""

import time
from typing import List, Optional
from dataclasses import dataclass, field
import numpy as np
import structlog

from .base_detector import BaseDetector, DetectionResult, DetectionBox, DetectionStatus

logger = structlog.get_logger("aegis.vision.knock")


@dataclass
class KnockInfo:
    """Knock/Kill event information"""
    event_id: str = ""
    timestamp: float = field(default_factory=time.time)
    
    # Event type
    event_type: str = ""  # "knock", "kill", "finish", "team_wipe"
    
    # Participants
    attacker_id: str = ""
    attacker_name: str = ""
    attacker_team: str = ""
    victim_id: str = ""
    victim_name: str = ""
    victim_team: str = ""
    
    # Details
    weapon: str = ""
    damage: int = 0
    headshot: bool = False
    distance: float = 0.0
    
    # Context
    is_team_wipe: bool = False
    is_clutch_situation: bool = False
    remaining_teammates: int = 4
    
    # Feed position
    feed_position: int = 0
    feed_text: str = ""
    
    # Detection
    confidence: float = 0.0
    source: str = ""  # "ocr", "hud", "combined"


class KnockDetector(BaseDetector):
    """
    Detects knock and kill events from BGMI.
    
    Distinguishes between:
    - Knock (player down but revivable)
    - Kill (player eliminated from match)
    - Finish (knocked player finished off)
    - Team wipe (last player eliminated)
    
    Uses kill feed OCR and HUD indicators.
    """
    
    def __init__(self):
        super().__init__(name="knock_detector")
        
        # Kill feed region
        self.feed_region = DetectionBox(
            x=0.75, y=0.05, width=0.23, height=0.40
        )
        
        # Knock indicators in kill feed
        self.knock_keywords = [
            "knocked", "knock", "down", "dnbo",
        ]
        
        self.kill_keywords = [
            "eliminated", "elim", "killed", "finished",
            "frag", "out",
        ]
        
        self.headshot_indicators = [
            "headshot", "hs", "head", "helmet",
        ]
        
        # Recent events for deduplication
        self.recent_events: List[KnockInfo] = []
        
        self._log = logger.bind(detector=self.name)
    
    async def detect(self, frame: np.ndarray, **kwargs) -> DetectionResult:
        """
        Detect knock/kill events from frame.
        
        Args:
            frame: Full game frame
            **kwargs: known_players, kill_feed_text
        """
        
        start_time = time.time()
        h, w = frame.shape[:2]
        
        # Get kill feed text from OCR (or passed directly)
        feed_entries = kwargs.get("kill_feed_entries", [])
        
        if not feed_entries:
            feed_entries = self._extract_feed_text(frame)
        
        # Parse events
        events = []
        for entry_text in feed_entries:
            event = self._parse_event(entry_text)
            if event:
                events.append(event)
        
        # Deduplicate
        events = self._deduplicate_events(events)
        
        result = DetectionResult(
            detector_name=self.name,
            status=DetectionStatus.SUCCESS if events else DetectionStatus.NOT_FOUND,
            frame_width=w,
            frame_height=h,
            processing_time_ms=self._time_detection(start_time),
            extra_data={
                "events_detected": len(events),
                "events": [
                    {
                        "type": e.event_type,
                        "attacker": e.attacker_name,
                        "victim": e.victim_name,
                        "weapon": e.weapon,
                        "headshot": e.headshot,
                        "team_wipe": e.is_team_wipe,
                        "confidence": round(e.confidence, 3),
                    }
                    for e in events
                ],
            },
        )
        
        self._last_result = result
        return result
    
    def _extract_feed_text(self, frame: np.ndarray) -> List[str]:
        """Extract kill feed text from frame"""
        
        entries = []
        
        try:
            import cv2
            
            h, w = frame.shape[:2]
            
            # Extract feed region
            x1 = int(self.feed_region.x * w)
            y1 = int(self.feed_region.y * h)
            x2 = int((self.feed_region.x + self.feed_region.width) * w)
            y2 = int((self.feed_region.y + self.feed_region.height) * h)
            
            feed_region = frame[y1:y2, x1:x2]
            
            if feed_region.size > 0:
                from paddleocr import PaddleOCR
                
                if not hasattr(self, '_ocr'):
                    self._ocr = PaddleOCR(
                        use_angle_cls=True,
                        lang='en',
                        use_gpu=True,
                        show_log=False,
                    )
                
                results = self._ocr.ocr(feed_region, cls=True)
                
                if results and results[0]:
                    for line in results[0]:
                        if line and len(line) >= 2:
                            entries.append(line[1][0])
            
        except ImportError:
            pass
        except Exception as e:
            self._log.error(f"Feed extraction error: {e}")
        
        return entries
    
    def _parse_event(self, text: str) -> Optional[KnockInfo]:
        """Parse kill feed text into event"""
        
        text = text.strip()
        if not text:
            return None
        
        event = KnockInfo(feed_text=text)
        
        # Determine event type
        text_lower = text.lower()
        
        for keyword in self.knock_keywords:
            if keyword in text_lower:
                event.event_type = "knock"
                break
        
        if not event.event_type:
            for keyword in self.kill_keywords:
                if keyword in text_lower:
                    event.event_type = "kill"
                    break
        
        if not event.event_type:
            event.event_type = "kill"  # Default
        
        # Check for headshot
        for indicator in self.headshot_indicators:
            if indicator in text_lower:
                event.headshot = True
                break
        
        # Check for team wipe
        if "squad" in text_lower or "team" in text_lower or "wipe" in text_lower:
            event.is_team_wipe = True
        
        # Extract names (simplified - assumes "Name weapon Name" format)
        parts = text.split()
        if len(parts) >= 3:
            event.attacker_name = parts[0]
            event.weapon = parts[1] if len(parts) > 1 else ""
            event.victim_name = parts[-1]
            event.confidence = 0.6
        elif len(parts) >= 2:
            event.attacker_name = parts[0]
            event.victim_name = parts[-1]
            event.confidence = 0.4
        else:
            event.confidence = 0.2
        
        return event
    
    def _deduplicate_events(self, events: List[KnockInfo]) -> List[KnockInfo]:
        """Remove duplicate events"""
        
        current_time = time.time()
        unique_events = []
        
        for event in events:
            is_duplicate = False
            
            for recent in self.recent_events:
                # Same attacker, victim, and type within 2 seconds
                if (event.attacker_name == recent.attacker_name and
                    event.victim_name == recent.victim_name and
                    event.event_type == recent.event_type and
                    current_time - recent.timestamp < 2.0):
                    is_duplicate = True
                    break
            
            if not is_duplicate:
                unique_events.append(event)
                self.recent_events.append(event)
        
        # Keep recent events list manageable
        self.recent_events = [
            e for e in self.recent_events
            if current_time - e.timestamp < 10.0
        ]
        
        return unique_events
    
    def get_detection_regions(self, frame_width: int, frame_height: int) -> List[DetectionBox]:
        """Get knock detection regions"""
        return [self.feed_region]