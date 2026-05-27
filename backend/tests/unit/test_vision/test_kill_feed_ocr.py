# backend/tests/unit/test_vision/test_kill_feed_ocr.py
"""
Unit tests for Kill Feed OCR Detector.
Comprehensive tests for kill feed parsing, player name extraction,
weapon detection, and kill type classification.
"""

import pytest
import numpy as np
from unittest.mock import AsyncMock, patch


@pytest.mark.unit
@pytest.mark.vision
class TestKillFeedOCR:
    """Test KillFeedOCR functionality"""
    
    @pytest.fixture
    def detector(self):
        """Create KillFeedOCR instance"""
        from app.vision.detectors.kill_feed_ocr import KillFeedOCR
        return KillFeedOCR()
    
    @pytest.fixture
    def detector_with_players(self):
        """Create KillFeedOCR with known players"""
        from app.vision.detectors.kill_feed_ocr import KillFeedOCR
        ocr = KillFeedOCR()
        ocr.known_players = {
            "ScoutOP": "Team Soul",
            "Mortal": "Team Soul",
            "Jonathan": "GodLike Esports",
            "ClutchGod": "GodLike Esports",
            "Snax": "Revenant Esports",
            "Goblin": "Revenant Esports",
            "Owais": "Orange Rock",
            "Mavi": "Orange Rock",
        }
        return ocr
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initialization(self, detector):
        """Test detector initializes correctly"""
        assert detector.name == "kill_feed_ocr"
        assert detector.feed_region is not None
        assert detector.feed_region.x == 0.75  # Right side of screen
        assert detector.feed_region.y == 0.05  # Top area
        assert len(detector.known_weapons) > 20  # Comprehensive weapon list
        assert "M416" in detector.known_weapons
        assert "AWM" in detector.known_weapons
        assert "Frag Grenade" in detector.known_weapons
    
    def test_get_detection_regions(self, detector):
        """Test getting kill feed detection regions"""
        regions = detector.get_detection_regions(1920, 1080)
        
        assert len(regions) == 1
        region = regions[0]
        assert region.x == int(0.75 * 1920)  # Right side
        assert region.y == int(0.05 * 1080)  # Top area
        assert region.width == int(0.23 * 1920)  # ~23% width
        assert region.height == int(0.40 * 1080)  # ~40% height
    
    def test_set_known_players(self, detector):
        """Test setting known player names"""
        players = {"ScoutOP": "Team Soul", "Jonathan": "GodLike"}
        detector.set_known_players(players)
        
        assert detector.known_players == players
        assert "ScoutOP" in detector.known_players
        assert detector.known_players["ScoutOP"] == "Team Soul"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # KILL FEED PARSING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_parse_standard_kill(self, detector):
        """Test parsing standard kill: 'Killer Weapon Victim'"""
        text = "ScoutOP M416 Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "ScoutOP"
        assert entry.weapon == "M416"
        assert entry.victim_name == "Jonathan"
        assert entry.kill_type == "kill"
        assert entry.is_valid is True
    
    def test_parse_kill_with_headshot(self, detector):
        """Test parsing kill with headshot indicator"""
        text = "ScoutOP AWM headshot Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "ScoutOP"
        assert entry.weapon == "AWM"
        assert entry.headshot is True
        assert entry.victim_name == "Jonathan"
    
    def test_parse_kill_with_hs_abbreviation(self, detector):
        """Test parsing kill with 'hs' abbreviation"""
        text = "Mortal AKM hs Goblin"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "Mortal"
        assert entry.headshot is True
    
    def test_parse_knock_event(self, detector):
        """Test parsing knock event"""
        text = "ScoutOP knocked Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "ScoutOP"
        assert entry.kill_type == "knock"
        assert entry.victim_name == "Jonathan"
    
    def test_parse_knock_variations(self, detector):
        """Test parsing various knock indicators"""
        knock_texts = [
            "ScoutOP knock Jonathan",
            "ScoutOP down Jonathan",
            "ScoutOP dnbo Jonathan",
        ]
        
        for text in knock_texts:
            entry = detector._parse_kill_entry(text)
            assert entry is not None, f"Failed to parse: {text}"
            assert entry.kill_type == "knock", f"Wrong type for: {text}"
    
    def test_parse_kill_variations(self, detector):
        """Test parsing various kill indicators"""
        kill_texts = [
            "ScoutOP eliminated Jonathan",
            "ScoutOP elim Jonathan",
            "ScoutOP killed Jonathan",
            "ScoutOP finished Jonathan",
        ]
        
        for text in kill_texts:
            entry = detector._parse_kill_entry(text)
            assert entry is not None, f"Failed to parse: {text}"
            assert entry.kill_type == "kill", f"Wrong type for: {text}"
    
    def test_parse_with_weapon_brackets(self, detector):
        """Test parsing with weapon in brackets"""
        text = "ScoutOP [M416] Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.weapon == "M416"
    
    def test_parse_all_weapons(self, detector):
        """Test parsing all known weapons"""
        for weapon in detector.known_weapons[:10]:  # Test first 10
            text = f"ScoutOP {weapon} Jonathan"
            
            entry = detector._parse_kill_entry(text)
            
            assert entry is not None, f"Failed for weapon: {weapon}"
            assert entry.weapon == weapon, f"Wrong weapon: expected {weapon}, got {entry.weapon}"
    
    def test_parse_with_known_players(self, detector_with_players):
        """Test parsing with known player names for team lookup"""
        detector = detector_with_players
        text = "ScoutOP M416 Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_team == "Team Soul"
        assert entry.victim_team == "GodLike Esports"
        assert entry.confidence > 0.8  # Higher confidence for known players
    
    def test_parse_with_unknown_players(self, detector):
        """Test parsing with unknown player names"""
        text = "Unknown1 M416 Unknown2"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "Unknown1"
        assert entry.victim_name == "Unknown2"
        assert entry.killer_team == ""
        assert entry.victim_team == ""
        assert entry.confidence < 0.8  # Lower confidence for unknown players
    
    def test_parse_grenade_kill(self, detector):
        """Test parsing grenade kill"""
        text = "ScoutOP Frag Grenade Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.weapon == "Frag Grenade"
    
    def test_parse_molotov_kill(self, detector):
        """Test parsing molotov kill"""
        text = "ScoutOP Molotov Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.weapon == "Molotov"
    
    def test_parse_melee_kill(self, detector):
        """Test parsing melee kill"""
        text = "ScoutOP Pan Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.weapon == "Pan"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EDGE CASES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_parse_empty_text(self, detector):
        """Test parsing empty text"""
        entry = detector._parse_kill_entry("")
        assert entry is None
    
    def test_parse_whitespace_text(self, detector):
        """Test parsing whitespace-only text"""
        entry = detector._parse_kill_entry("   ")
        assert entry is None
    
    def test_parse_very_short_text(self, detector):
        """Test parsing very short text"""
        entry = detector._parse_kill_entry("Hi")
        assert entry is None
    
    def test_parse_single_word(self, detector):
        """Test parsing single word"""
        entry = detector._parse_kill_entry("ScoutOP")
        assert entry is None or entry.is_valid is False
    
    def test_parse_two_words(self, detector):
        """Test parsing two words (no weapon)"""
        text = "ScoutOP Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "ScoutOP"
        assert entry.victim_name == "Jonathan"
        assert entry.weapon == ""
        assert entry.confidence < 0.5  # Lower confidence without weapon
    
    def test_parse_case_insensitive(self, detector):
        """Test parsing is case insensitive"""
        text = "scoutop m416 jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "scoutop"
        assert entry.weapon == "M416"
    
    def test_parse_with_extra_spaces(self, detector):
        """Test parsing with extra whitespace"""
        text = "  ScoutOP   M416   Jonathan  "
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "ScoutOP"
        assert entry.weapon == "M416"
    
    def test_parse_with_special_characters(self, detector):
        """Test parsing names with special characters"""
        text = "ClutchGod_YT M416 Jonathan_OP"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        # Should handle underscores in names
    
    def test_parse_player_with_numbers(self, detector):
        """Test parsing names with numbers"""
        text = "Player123 M416 Enemy456"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        assert entry.killer_name == "Player123"
        assert entry.victim_name == "Enemy456"
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DETECTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_detect_with_empty_frame(self, detector):
        """Test detection on empty frame"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        result = await detector.detect(frame)
        
        assert result is not None
        assert result.detector_name == "kill_feed_ocr"
        assert result.status.value in ["not_found", "failed"]
        assert result.count == 0
    
    @pytest.mark.asyncio
    async def test_detect_with_mock_ocr(self, detector):
        """Test detection with mocked OCR results"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        # Mock the OCR method
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = [
                "ScoutOP M416 Jonathan",
                "Mortal AKM headshot Snax",
                "ClutchGod knocked Goblin",
            ]
            
            result = await detector.detect(frame)
            
            assert result is not None
            assert result.status.value == "success"
            
            # Check extracted entries
            entries = result.extra_data.get("entries", [])
            assert len(entries) == 3
            assert entries[0]["killer"] == "ScoutOP"
            assert entries[0]["weapon"] == "M416"
            assert entries[1]["headshot"] is True
            assert entries[2]["kill_type"] == "knock"
    
    @pytest.mark.asyncio
    async def test_detect_with_mock_ocr_and_known_players(self, detector_with_players):
        """Test detection with mocked OCR and known players"""
        detector = detector_with_players
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = [
                "ScoutOP M416 Jonathan",
            ]
            
            result = await detector.detect(frame)
            
            assert result is not None
            entries = result.extra_data.get("entries", [])
            assert len(entries) == 1
            assert entries[0]["killer"] == "ScoutOP"
            # Team info would be in the parsed entry
    
    @pytest.mark.asyncio
    async def test_detect_with_no_ocr_results(self, detector):
        """Test detection when OCR returns nothing"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = []
            
            result = await detector.detect(frame)
            
            assert result is not None
            assert result.status.value == "not_found"
    
    @pytest.mark.asyncio
    async def test_detect_with_ocr_error(self, detector):
        """Test detection when OCR raises an error"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.side_effect = Exception("OCR engine error")
            
            result = await detector.detect(frame)
            
            assert result is not None
            # Should handle error gracefully
            assert result.status.value in ["not_found", "failed"]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROCESSING TIME TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_detection_timing(self, detector):
        """Test that detection timing is recorded"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = ["ScoutOP M416 Jonathan"]
            
            result = await detector.detect(frame)
            
            assert result.processing_time_ms > 0
            assert result.processing_time_ms < 5000  # Should not take >5 seconds
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_detection_statistics(self, detector):
        """Test that detection statistics are updated"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
            mock_ocr.return_value = ["ScoutOP M416 Jonathan"]
            
            for _ in range(5):
                await detector.detect(frame)
        
        stats = detector.get_stats()
        
        assert stats["total_detections"] == 5
        assert stats["detector"] == "kill_feed_ocr"
        assert stats["avg_processing_time_ms"] > 0
    
    def test_reset(self, detector):
        """Test resetting detector statistics"""
        detector._total_detections = 100
        detector._total_processing_time = 5000.0
        
        detector.reset()
        
        assert detector._total_detections == 0
        assert detector._total_processing_time == 0.0
        assert detector._last_result is None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMPLEX SCENARIOS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_detect_multiple_kills_sequence(self, detector):
        """Test detecting a sequence of kills like in real match"""
        frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
        
        # Simulate kill feed entries appearing over time
        kill_sequences = [
            ["ScoutOP M416 Jonathan"],
            ["ScoutOP M416 Jonathan", "Mortal AKM Snax"],
            ["Mortal AKM Snax", "ClutchGod AWM headshot Goblin"],
            ["ClutchGod AWM headshot Goblin"],
        ]
        
        for kills in kill_sequences:
            with patch.object(detector, '_ocr_feed', new_callable=AsyncMock) as mock_ocr:
                mock_ocr.return_value = kills
                result = await detector.detect(frame)
                assert result is not None
    
    @pytest.mark.asyncio
    async def test_detect_with_noisy_frame(self, detector):
        """Test detection with noisy/interference frame"""
        # Create frame with random noise
        frame = np.random.randint(0, 255, (1080, 1920, 3), dtype=np.uint8)
        
        result = await detector.detect(frame)
        
        assert result is not None
        # Should handle noisy frames gracefully
    
    def test_parse_team_wipe_indicator(self, detector):
        """Test parsing team wipe notification"""
        text = "ScoutOP squad wipe eliminated"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        # Team wipe should be detected
    
    def test_parse_first_blood(self, detector):
        """Test parsing first blood notification"""
        # First blood usually has special formatting
        text = "ScoutOP M416 Jonathan first blood"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
    
    def test_parse_long_range_kill(self, detector):
        """Test parsing kill with distance info"""
        text = "ScoutOP AWM 300m Jonathan"
        
        entry = detector._parse_kill_entry(text)
        
        assert entry is not None
        # Should still extract names and weapon
        assert entry.killer_name == "ScoutOP"
        assert entry.weapon == "AWM"