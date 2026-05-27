# backend/tests/unit/test_vision/test_player_tracker.py
"""
Unit tests for Player Tracker.
Tests multi-object tracking, ID assignment, and movement analysis.
"""

import pytest
import numpy as np


@pytest.mark.unit
@pytest.mark.vision
class TestPlayerTracker:
    """Test PlayerTracker functionality"""
    
    @pytest.fixture
    def tracker(self):
        from app.vision.detectors.player_tracker import PlayerTracker
        return PlayerTracker()
    
    def test_initialization(self, tracker):
        """Test tracker initialization"""
        assert tracker.name == "player_tracker"
        assert tracker.max_lost_frames == 30
        assert tracker.track_distance_threshold == 100
        assert len(tracker.tracked_players) == 0
    
    def test_get_detection_regions(self, tracker):
        """Test getting detection regions"""
        regions = tracker.get_detection_regions(1920, 1080)
        
        assert len(regions) == 1
        region = regions[0]
        assert region.width == 1.0
        assert region.height == 1.0
    
    @pytest.mark.asyncio
    async def test_detect_empty_frame(self, tracker):
        """Test tracking on empty frame"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        result = await tracker.detect(frame)
        
        assert result is not None
        assert result.detector_name == "player_tracker"
        assert result.count == 0
    
    def test_update_tracks_new(self, tracker):
        """Test updating tracks with new detections"""
        from app.vision.detectors.base_detector import DetectionBox
        
        detections = [
            DetectionBox(x=100, y=200, width=50, height=100, confidence=0.9, label="player"),
            DetectionBox(x=300, y=400, width=50, height=100, confidence=0.85, label="player"),
        ]
        
        tracker._update_tracks(detections, 1920, 1080)
        
        assert len(tracker.tracked_players) == 2
    
    def test_update_tracks_existing(self, tracker):
        """Test updating existing tracks"""
        from app.vision.detectors.base_detector import DetectionBox
        from app.vision.detectors.player_tracker import TrackedPlayer
        
        # Add existing track
        existing = TrackedPlayer(
            track_id=1,
            player_id="player_1",
            position_x=100,
            position_y=200,
        )
        tracker.tracked_players[1] = existing
        
        # Update with nearby detection
        detections = [
            DetectionBox(x=105, y=205, width=50, height=100, confidence=0.9, label="player"),
        ]
        
        tracker._update_tracks(detections, 1920, 1080)
        
        assert len(tracker.tracked_players) == 1
        assert tracker.tracked_players[1].position_x == 105
        assert tracker.tracked_players[1].position_y == 205
        assert tracker.tracked_players[1].is_moving
    
    def test_update_tracks_lost(self, tracker):
        """Test handling lost tracks"""
        from app.vision.detectors.player_tracker import TrackedPlayer
        
        # Add existing track
        existing = TrackedPlayer(
            track_id=1,
            player_id="player_1",
            position_x=100,
            position_y=200,
            frames_lost=29,
        )
        tracker.tracked_players[1] = existing
        
        # Update with no matching detection
        tracker._update_tracks([], 1920, 1080)
        
        # Track should be removed after max_lost_frames
        assert len(tracker.tracked_players) == 0
    
    def test_get_tracked_players(self, tracker):
        """Test getting tracked players"""
        from app.vision.detectors.player_tracker import TrackedPlayer
        
        tracker.tracked_players = {
            1: TrackedPlayer(track_id=1, player_id="p1", position_x=100, position_y=200),
            2: TrackedPlayer(track_id=2, player_id="p2", position_x=300, position_y=400),
        }
        
        players = tracker.get_tracked_players()
        
        assert len(players) == 2
    
    def test_get_player_by_id(self, tracker):
        """Test getting player by track ID"""
        from app.vision.detectors.player_tracker import TrackedPlayer
        
        player = TrackedPlayer(track_id=5, player_id="player_5")
        tracker.tracked_players[5] = player
        
        result = tracker.get_player_by_id(5)
        
        assert result is not None
        assert result.player_id == "player_5"
        
        result = tracker.get_player_by_id(999)
        assert result is None
    
    def test_get_player_positions(self, tracker):
        """Test getting all player positions"""
        from app.vision.detectors.player_tracker import TrackedPlayer
        
        tracker.tracked_players = {
            1: TrackedPlayer(track_id=1, position_x=100, position_y=200),
            2: TrackedPlayer(track_id=2, position_x=300, position_y=400),
        }
        
        positions = tracker.get_player_positions()
        
        assert len(positions) == 2
        assert positions[1] == (100, 200)
        assert positions[2] == (300, 400)