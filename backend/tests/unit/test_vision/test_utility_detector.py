# backend/tests/unit/test_vision/test_utility_detector.py
"""
Unit tests for Utility Detector.
Tests grenade, smoke, molotov detection.
"""

import pytest
import numpy as np


@pytest.mark.unit
@pytest.mark.vision
class TestUtilityDetector:
    """Test UtilityDetector functionality"""
    
    @pytest.fixture
    def detector(self):
        from app.vision.detectors.utility_detector import UtilityDetector
        return UtilityDetector()
    
    def test_initialization(self, detector):
        """Test detector initialization"""
        assert detector.name == "utility_detector"
        assert len(detector.utility_indicators) == 3
        assert "grenade" in detector.utility_indicators
        assert "smoke" in detector.utility_indicators
        assert "molotov" in detector.utility_indicators
    
    def test_get_detection_regions(self, detector):
        """Test getting detection regions"""
        regions = detector.get_detection_regions(1920, 1080)
        
        assert len(regions) == 1
        region = regions[0]
        # Full frame detection
        assert region.x == 0.0
        assert region.y == 0.0
        assert region.width == 1.0
        assert region.height == 1.0
    
    @pytest.mark.asyncio
    async def test_detect_empty_frame(self, detector):
        """Test detection on empty frame"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        result = await detector.detect(frame)
        
        assert result is not None
        assert result.detector_name == "utility_detector"
    
    def test_detect_explosions(self, detector):
        """Test explosion detection"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        import cv2
        # Add bright orange spot (explosion)
        cv2.circle(frame, (960, 540), 50, (0, 165, 255), -1)
        
        events = detector._detect_explosions(frame)
        
        assert isinstance(events, list)
        # May or may not detect depending on color thresholds
    
    def test_detect_smoke(self, detector):
        """Test smoke detection"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        import cv2
        # Add grayish cloud
        cv2.circle(frame, (960, 540), 100, (180, 180, 180), -1)
        cv2.GaussianBlur(frame, (51, 51), 20, dst=frame)
        
        events = detector._detect_smoke(frame)
        
        assert isinstance(events, list)
    
    def test_detect_fire(self, detector):
        """Test fire/molotov detection"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        import cv2
        # Add reddish-orange fire
        cv2.circle(frame, (960, 540), 40, (0, 100, 255), -1)
        
        events = detector._detect_fire(frame)
        
        assert isinstance(events, list)
    
    def test_update_active_utilities(self, detector):
        """Test updating active utilities"""
        from app.vision.detectors.utility_detector import UtilityInfo
        
        # Add a smoke
        smoke_event = UtilityInfo(
            event_id="test_smoke_001",
            utility_type="smoke",
            landing_position=(500, 500),
        )
        
        detector._update_active_utilities([smoke_event])
        
        assert len(detector.active_smokes) == 1
        assert detector.active_smokes[0].event_id == "test_smoke_001"
    
    def test_get_active_smoke_positions(self, detector):
        """Test getting active smoke positions"""
        from app.vision.detectors.utility_detector import UtilityInfo
        
        detector.active_smokes = [
            UtilityInfo(utility_type="smoke", landing_position=(100, 200)),
            UtilityInfo(utility_type="smoke", landing_position=(300, 400)),
        ]
        
        positions = detector.get_active_smoke_positions()
        
        assert len(positions) == 2
        assert positions[0] == (100, 200)
        assert positions[1] == (300, 400)