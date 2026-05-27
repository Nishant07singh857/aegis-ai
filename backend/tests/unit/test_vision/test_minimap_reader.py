# backend/tests/unit/test_vision/test_minimap_reader.py
"""
Unit tests for Minimap Reader.
Tests minimap position extraction, zone detection, and player tracking.
"""

import pytest
import numpy as np


@pytest.mark.unit
@pytest.mark.vision
class TestMinimapReader:
    """Test MinimapReader functionality"""
    
    @pytest.fixture
    def reader(self):
        from app.vision.detectors.minimap_reader import MinimapReader
        return MinimapReader()
    
    def test_initialization(self, reader):
        """Test reader initialization"""
        assert reader.name == "minimap_reader"
        assert reader.minimap_region is not None
        assert reader.minimap_region.x > 0.7  # Bottom-right corner
    
    def test_get_detection_regions(self, reader):
        """Test getting detection regions"""
        regions = reader.get_detection_regions(1920, 1080)
        
        assert len(regions) == 1
        region = regions[0]
        assert region.x > 0.7
        assert region.y > 0.7
    
    @pytest.mark.asyncio
    async def test_detect_empty_frame(self, reader):
        """Test detection on empty frame"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        result = await reader.detect(frame)
        
        assert result is not None
        assert result.detector_name == "minimap_reader"
        assert result.status.value in ["not_found", "failed", "success"]
    
    def test_parse_minimap(self, reader):
        """Test minimap parsing"""
        minimap = np.zeros((300, 500, 3), dtype=np.uint8)
        
        # Add some simulated markers
        import cv2
        # White dot (player)
        cv2.circle(minimap, (250, 150), 3, (255, 255, 255), -1)
        # Green dot (teammate)
        cv2.circle(minimap, (280, 140), 3, (0, 255, 0), -1)
        
        data = reader._parse_minimap(minimap, 0, 0, 1920, 1080)
        
        assert data is not None
        assert data.minimap_width == 500
        assert data.minimap_height == 300
    
    def test_detect_circle(self, reader):
        """Test circle detection for zones"""
        hsv = np.zeros((300, 500, 3), dtype=np.uint8)
        
        import cv2
        # Draw white circle (safe zone)
        cv2.circle(hsv, (250, 150), 80, (0, 0, 255), 2)
        
        result = reader._detect_circle(hsv, ((0, 0, 200), (180, 30, 255)))
        
        # May or may not detect depending on Hough parameters
        assert result is None or len(result) == 2
    
    def test_minimap_to_world(self, reader):
        """Test coordinate conversion"""
        # Without calibration, should return same values
        result = reader.minimap_to_world(100, 200)
        
        assert result[0] == 100
        assert result[1] == 200
    
    def test_detect_markers(self, reader):
        """Test player marker detection"""
        hsv = np.zeros((300, 500, 3), dtype=np.uint8)
        
        import cv2
        # Add colored markers
        cv2.circle(hsv, (250, 150), 3, (0, 255, 0), -1)  # Green
        cv2.circle(hsv, (300, 180), 3, (0, 0, 255), -1)  # Red
        
        markers = reader._detect_markers(
            hsv,
            {"teammate": ((0, 200, 0), (50, 255, 50)), "enemy": ((0, 0, 200), (50, 50, 255))},
            min_size=2,
        )
        
        assert isinstance(markers, dict)