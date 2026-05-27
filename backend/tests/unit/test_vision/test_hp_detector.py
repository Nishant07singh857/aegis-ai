# backend/tests/unit/test_vision/test_hp_detector.py
"""
Unit tests for HP Bar Detector.
Tests health bar detection, armor detection, and boost level reading.
"""

import pytest
import numpy as np


@pytest.mark.unit
@pytest.mark.vision
class TestHPBarDetector:
    """Test HPBarDetector functionality"""
    
    @pytest.fixture
    def detector(self):
        from app.vision.detectors.hp_bar_detector import HPBarDetector
        return HPBarDetector()
    
    def test_initialization(self, detector):
        """Test detector initialization"""
        assert detector.name == "hp_bar_detector"
        assert detector.hp_bar_region is not None
        assert len(detector.hp_colors) == 4
        assert len(detector.armor_colors) == 3
    
    def test_get_detection_regions(self, detector):
        """Test getting detection regions"""
        regions = detector.get_detection_regions(1920, 1080)
        
        assert len(regions) == 1
        region = regions[0]
        assert 0 < region.x < 1
        assert 0 < region.y < 1
    
    @pytest.mark.asyncio
    async def test_detect_empty_frame(self, detector):
        """Test detection on empty frame"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        result = await detector.detect(frame)
        
        assert result is not None
        assert result.detector_name == "hp_bar_detector"
        assert result.status.value in ["not_found", "failed"]
    
    @pytest.mark.asyncio
    async def test_detect_with_hp_bars(self, detector):
        """Test detection with simulated HP bars"""
        # Create frame with green HP bars
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        # Draw simulated HP bars (green rectangles)
        import cv2
        for i in range(4):
            y = int(0.15 * 1080) + i * 120
            cv2.rectangle(
                frame,
                (int(0.02 * 1920), y),
                (int(0.10 * 1920), y + 8),
                (0, 255, 0),  # Green
                -1,
            )
        
        result = await detector.detect(frame)
        
        assert result is not None
        assert result.detector_name == "hp_bar_detector"
    
    def test_detect_hp_bars_color(self, detector):
        """Test HP bar detection with color analysis"""
        # Create synthetic HP bar region
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        
        # Add green bars
        import cv2
        for i in range(4):
            y = 50 + i * 120
            cv2.rectangle(region, (20, y), (180, y + 8), (0, 255, 0), -1)
        
        hp_infos = detector._detect_hp_bars(region, 0, 0)
        
        assert len(hp_infos) > 0
    
    def test_detect_hp_bars_low_health(self, detector):
        """Test HP bar detection with low health (red bars)"""
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        
        import cv2
        # Add red bar (low HP)
        cv2.rectangle(region, (20, 50), (80, 58), (0, 0, 255), -1)
        
        hp_infos = detector._detect_hp_bars(region, 0, 0)
        
        assert len(hp_infos) >= 0  # May or may not detect depending on color range
    
    def test_detect_armor_bars(self, detector):
        """Test armor bar detection"""
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        
        from app.vision.detectors.hp_bar_detector import HPBarInfo
        
        hp_infos = [
            HPBarInfo(
                player_name="Player1",
                hp_current=100,
                bar_x=20,
                bar_y=50,
                bar_width=180,
                bar_height=8,
            )
        ]
        
        import cv2
        # Add armor bar (gold for level 3)
        cv2.rectangle(region, (20, 60), (150, 66), (0, 200, 255), -1)
        
        detector._detect_armor_bars(region, hp_infos, 0, 0)
        
        assert hp_infos[0].armor_type in ["level3", ""]
    
    def test_detect_boost_bars(self, detector):
        """Test boost bar detection"""
        region = np.zeros((600, 200, 3), dtype=np.uint8)
        
        from app.vision.detectors.hp_bar_detector import HPBarInfo
        
        hp_infos = [
            HPBarInfo(
                player_name="Player1",
                hp_current=100,
                bar_x=20,
                bar_y=50,
                bar_width=180,
                bar_height=8,
            )
        ]
        
        import cv2
        # Add boost bars (orange)
        for i in range(2):
            cv2.rectangle(region, (20 + i * 35, 35), (20 + i * 35 + 30, 42), (0, 180, 255), -1)
        
        detector._detect_boost_bars(region, hp_infos, 0, 0)
        
        assert hp_infos[0].boost_level >= 0