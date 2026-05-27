# backend/tests/unit/test_utils/test_coordinate_mapper.py
"""
Unit tests for Coordinate Mapper.
Tests screen-to-world coordinate conversion, minimap calibration,
distance calculations, and position interpolation.
"""

import pytest

from app.vision.post_processors.coordinate_mapper import (
    CoordinateMapper,
)


@pytest.mark.unit
@pytest.mark.utils
class TestCoordinateMapper:
    """Test CoordinateMapper functionality"""
    
    @pytest.fixture
    def mapper(self):
        """Create CoordinateMapper instance"""
        return CoordinateMapper()
    
    @pytest.fixture
    def calibrated_mapper(self):
        """Create calibrated CoordinateMapper"""
        mapper = CoordinateMapper()
        mapper.calibrate(
            map_name="erangel",
            screen_width=1920,
            screen_height=1080,
            known_world_position=(4000.0, 5000.0),
            known_screen_position=(1500, 400),
            minimap_region=(0.72, 0.70, 0.26, 0.28),
        )
        return mapper
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, mapper):
        """Test mapper initializes with default values"""
        assert mapper.calibration.map_name == "erangel"
        assert mapper.calibration.map_width_meters == 8000.0
        assert mapper.calibration.map_height_meters == 8000.0
        assert mapper.calibration.is_calibrated is False
    
    def test_map_configs_loaded(self, mapper):
        """Test all map configurations are loaded"""
        expected_maps = ["erangel", "miramar", "sanhok", "vikendi", "livik", "karakin"]
        
        for map_name in expected_maps:
            assert map_name in mapper.map_configs
            assert "width" in mapper.map_configs[map_name]
            assert "height" in mapper.map_configs[map_name]
    
    def test_map_sizes_correct(self, mapper):
        """Test map sizes are correct"""
        assert mapper.map_configs["erangel"]["width"] == 8000
        assert mapper.map_configs["erangel"]["height"] == 8000
        assert mapper.map_configs["sanhok"]["width"] == 4000
        assert mapper.map_configs["livik"]["width"] == 2000
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CALIBRATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_calibrate_success(self, calibrated_mapper):
        """Test successful calibration"""
        assert calibrated_mapper.calibration.is_calibrated is True
        assert calibrated_mapper.calibration.map_name == "erangel"
        assert calibrated_mapper.calibration.screen_width == 1920
        assert calibrated_mapper.calibration.screen_height == 1080
        assert calibrated_mapper.calibration.calibration_confidence > 0
    
    def test_calibrate_unknown_map(self, mapper):
        """Test calibration with unknown map name"""
        result = mapper.calibrate(
            map_name="unknown_map",
            screen_width=1920,
            screen_height=1080,
            known_world_position=(4000, 5000),
            known_screen_position=(1500, 400),
        )
        
        assert result is False
        assert mapper.calibration.is_calibrated is False
    
    def test_calibrate_stores_reference_point(self, calibrated_mapper):
        """Test calibration stores reference points"""
        assert len(calibrated_mapper.reference_points) == 1
        assert calibrated_mapper.reference_points[0]["world"] == (4000.0, 5000.0)
        assert calibrated_mapper.reference_points[0]["screen"] == (1500, 400)
    
    def test_add_reference_point(self, calibrated_mapper):
        """Test adding reference points"""
        calibrated_mapper.add_reference_point(
            world_pos=(4500.0, 5500.0),
            screen_pos=(1600, 500),
            confidence=0.9,
        )
        
        assert len(calibrated_mapper.reference_points) == 2
    
    def test_reference_points_limit(self, calibrated_mapper):
        """Test reference points are limited"""
        for i in range(25):
            calibrated_mapper.add_reference_point(
                world_pos=(4000 + i * 10, 5000 + i * 10),
                screen_pos=(1500 + i, 400 + i),
            )
        
        assert len(calibrated_mapper.reference_points) == 20  # Limited to 20
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COORDINATE CONVERSION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_minimap_to_world_calibrated(self, calibrated_mapper):
        """Test minimap to world conversion when calibrated"""
        result = calibrated_mapper.minimap_to_world(
            minimap_x=250,  # Center-ish of minimap
            minimap_y=150,
        )
        
        assert result is not None
        assert result.world_x != result.screen_x  # Should be different
        assert result.confidence > 0
    
    def test_minimap_to_world_uncalibrated(self, mapper):
        """Test minimap to world conversion without calibration"""
        result = mapper.minimap_to_world(100, 200)
        
        assert result is not None
        assert result.world_x == 100  # Same as input when uncalibrated
        assert result.world_y == 200
        assert result.confidence < 0.5  # Low confidence
    
    def test_screen_to_world_on_minimap(self, calibrated_mapper):
        """Test screen to world conversion for point on minimap"""
        # Point within minimap region
        screen_x = int(0.80 * 1920)  # 80% across
        screen_y = int(0.80 * 1080)  # 80% down
        
        result = calibrated_mapper.screen_to_world(screen_x, screen_y)
        
        assert result is not None
        assert result.source == "minimap"
    
    def test_screen_to_world_off_minimap(self, calibrated_mapper):
        """Test screen to world conversion for point off minimap"""
        # Point outside minimap region
        screen_x = 100
        screen_y = 100
        
        result = calibrated_mapper.screen_to_world(screen_x, screen_y)
        
        assert result is not None
        assert result.source in ["interpolated", "uncalibrated"]
    
    def test_world_to_screen(self, calibrated_mapper):
        """Test world to screen conversion"""
        screen_x, screen_y = calibrated_mapper.world_to_screen(4000, 5000)
        
        assert screen_x > 0
        assert screen_y > 0
        assert screen_x < 1920
        assert screen_y < 1080
    
    def test_world_to_screen_uncalibrated(self, mapper):
        """Test world to screen without calibration"""
        screen_x, screen_y = mapper.world_to_screen(4000, 5000)
        
        assert screen_x == 4000  # Returns same values
        assert screen_y == 5000
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DISTANCE CALCULATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_distance_world(self, mapper):
        """Test world distance calculation"""
        distance = mapper.distance_world((0, 0), (3000, 4000))
        
        assert distance == 5000.0  # 3-4-5 triangle
    
    def test_distance_world_same_point(self, mapper):
        """Test distance between same point"""
        distance = mapper.distance_world((1000, 2000), (1000, 2000))
        
        assert distance == 0.0
    
    def test_distance_screen(self, mapper):
        """Test screen distance calculation"""
        distance = mapper.distance_screen((0, 0), (300, 400))
        
        assert distance == 500.0  # 3-4-5 triangle
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ZONE CHECK TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_is_in_safe_zone_inside(self, mapper):
        """Test position inside safe zone"""
        result = mapper.is_in_safe_zone(
            world_x=4000,
            world_y=5000,
            zone_center=(4000, 5000),
            zone_radius=1000,
        )
        
        assert result is True
    
    def test_is_in_safe_zone_outside(self, mapper):
        """Test position outside safe zone"""
        result = mapper.is_in_safe_zone(
            world_x=4000,
            world_y=5000,
            zone_center=(4000, 5000),
            zone_radius=500,
        )
        
        # At exactly 500 radius, it's on the edge
        # At distance 0 from center, it's inside
        
        result_far = mapper.is_in_safe_zone(
            world_x=4000,
            world_y=5000,
            zone_center=(2000, 5000),
            zone_radius=500,
        )
        
        assert result_far is False
    
    def test_is_in_safe_zone_on_edge(self, mapper):
        """Test position on safe zone edge"""
        result = mapper.is_in_safe_zone(
            world_x=5000,
            world_y=5000,
            zone_center=(4000, 5000),
            zone_radius=1000,
        )
        
        assert result is True  # Exactly on edge is inside
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAP BOUNDS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_map_bounds(self, mapper):
        """Test getting map bounds"""
        bounds = mapper.get_map_bounds()
        
        assert bounds == (0, 0, 8000, 8000)  # Default Erangel
    
    def test_get_map_bounds_after_calibration(self, calibrated_mapper):
        """Test map bounds after calibration"""
        bounds = calibrated_mapper.get_map_bounds()
        
        assert bounds == (0, 0, 8000, 8000)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER POSITION UPDATE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_update_player_position(self, calibrated_mapper):
        """Test updating player position"""
        calibrated_mapper.update_player_position(4500.0, 5500.0)
        
        assert calibrated_mapper.calibration.world_offset_x == 4500.0
        assert calibrated_mapper.calibration.world_offset_y == 5500.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONVERSION ACCURACY TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_roundtrip_conversion(self, calibrated_mapper):
        """Test world → screen → world roundtrip"""
        world_x, world_y = 4500.0, 5500.0
        
        screen_x, screen_y = calibrated_mapper.world_to_screen(world_x, world_y)
        result = calibrated_mapper.screen_to_world(screen_x, screen_y)
        
        # Should be close to original (within minimap region)
        if result.source == "minimap":
            assert abs(result.world_x - world_x) < 200  # Within 200 meters
    
    def test_multiple_reference_points_accuracy(self, calibrated_mapper):
        """Test accuracy improves with more reference points"""
        # Add reference points around the map
        reference_points = [
            ((2000, 3000), (1200, 300)),
            ((6000, 3000), (1700, 300)),
            ((2000, 7000), (1200, 800)),
            ((6000, 7000), (1700, 800)),
        ]
        
        for world_pos, screen_pos in reference_points:
            calibrated_mapper.add_reference_point(world_pos, screen_pos)
        
        # Test interpolation for a point
        result = calibrated_mapper.screen_to_world(1500, 550)
        
        assert result is not None
        assert result.confidence > 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_get_stats(self, calibrated_mapper):
        """Test getting coordinate mapper statistics"""
        stats = calibrated_mapper.get_stats()
        
        assert "calibration" in stats
        assert stats["calibration"]["map"] == "erangel"
        assert stats["calibration"]["is_calibrated"] is True
        assert "reference_points" in stats
    
    def test_reset(self, calibrated_mapper):
        """Test resetting coordinate mapper"""
        calibrated_mapper.reset()
        
        assert calibrated_mapper.calibration.is_calibrated is False
        assert len(calibrated_mapper.reference_points) == 0