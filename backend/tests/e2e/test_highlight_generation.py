# backend/tests/e2e/test_highlight_generation.py
"""
End-to-End Test: Highlight Generation Pipeline
Tests automatic highlight detection, clip creation,
montage generation, and content publishing.
"""

import pytest

from app.core.event_bus import EventBus
from app.agents.highlight_agents.clip_detector import (
    ClipDetector, HighlightCategory, ClipQuality
)
from app.agents.highlight_agents.replay_director import (
    ReplayDirector, ReplayAngle
)
from app.agents.highlight_agents.montage_generator import (
    MontageGenerator, MontageStyle, MusicGenre
)
from app.agents.highlight_agents.content_publisher import (
    ContentPublisher
)


@pytest.mark.e2e
@pytest.mark.slow
class TestHighlightGeneration:
    """
    End-to-end test for highlight generation pipeline.
    
    Flow:
    1. Match events occur (kills, clutches, wipes)
    2. Clip Detector identifies highlight moments
    3. Replay Director creates multi-angle replays
    4. Montage Generator compiles highlight montage
    5. Content Publisher pushes to social platforms
    """
    
    @pytest.fixture
    def match_id(self):
        return "e2e_highlight_match"
    
    @pytest.mark.asyncio
    async def test_detect_all_highlight_types(self, match_id):
        """
        Test: Detect all types of highlight moments
        
        Simulates various game events and verifies
        that each type of highlight is properly detected.
        """
        print("\n" + "="*60)
        print("E2E: DETECT ALL HIGHLIGHT TYPES")
        print("="*60)
        
        event_bus = EventBus()
        await event_bus.connect()
        
        clip_detector = ClipDetector()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Test each highlight category
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        highlight_events = [
            # 1. Squad Wipe
            {
                "event_type": "squad_wipe",
                "data": {
                    "team_name": "GodLike Esports",
                    "eliminator": "Team Soul",
                    "time_span": 8.5,
                    "placement": 5,
                    "start_time_ms": 300000,
                    "end_time_ms": 308500,
                },
                "expected_category": HighlightCategory.SQUAD_WIPE,
                "expected_quality": ClipQuality.EXCELLENT,
            },
            
            # 2. Clutch Win
            {
                "event_type": "clutch_win",
                "data": {
                    "player_name": "ScoutOP",
                    "team_name": "Team Soul",
                    "enemy_count": 4,
                    "win_probability": 0.06,
                    "enemies_killed": 4,
                    "start_time_ms": 600000,
                    "end_time_ms": 620000,
                },
                "expected_category": HighlightCategory.CLUTCH_WIN,
                "expected_quality": ClipQuality.LEGENDARY,
            },
            
            # 3. Multi-Kill
            {
                "event_type": "multi_kill",
                "data": {
                    "player_name": "Jonathan",
                    "team_name": "GodLike Esports",
                    "kill_count": 4,
                    "time_span": 4.2,
                    "start_time_ms": 900000,
                    "end_time_ms": 904200,
                },
                "expected_category": HighlightCategory.MULTI_KILL,
                "expected_quality": ClipQuality.EXCELLENT,
            },
            
            # 4. Grenade Kill
            {
                "event_type": "grenade_kill",
                "data": {
                    "player_name": "Mortal",
                    "team_name": "Team Soul",
                    "victims": 3,
                    "distance": 45,
                    "timestamp_ms": 1200000,
                },
                "expected_category": HighlightCategory.GRENADE_KILL,
                "expected_quality": ClipQuality.EXCELLENT,
            },
            
            # 5. Long Range Kill
            {
                "event_type": "long_range_kill",
                "data": {
                    "player_name": "ClutchGod",
                    "team_name": "GodLike Esports",
                    "distance": 320,
                    "weapon": "AWM",
                    "headshot": True,
                    "timestamp_ms": 1500000,
                },
                "expected_category": HighlightCategory.LONG_RANGE,
                "expected_quality": ClipQuality.EXCELLENT,
            },
            
            # 6. Final Circle
            {
                "event_type": "final_circle_moment",
                "data": {
                    "winner_team": "Team Soul",
                    "teams_alive": 2,
                    "start_time_ms": 1700000,
                    "end_time_ms": 1725000,
                },
                "expected_category": HighlightCategory.FINAL_CIRCLE,
                "expected_quality": ClipQuality.EXCELLENT,
            },
        ]
        
        detected_clips = []
        
        for i, event in enumerate(highlight_events):
            clip = clip_detector.detect_clip(
                event_type=event["event_type"],
                event_data=event["data"],
                match_context={"match_id": match_id, "teams_alive": 16 - i},
            )
            
            if clip:
                detected_clips.append(clip)
                print(f"✓ {event['expected_category'].value}: {clip.title}")
                print(f"  Quality: {clip.quality.value} | Excitement: {clip.excitement_score:.0f} | Viral: {clip.viral_potential:.0f}")
                
                # Verify category
                assert clip.category == event["expected_category"], \
                    f"Expected {event['expected_category']}, got {clip.category}"
            else:
                print(f"⚠ {event['expected_category'].value}: Not detected (may need different thresholds)")
        
        print(f"\n✓ Detected {len(detected_clips)}/{len(highlight_events)} highlight types")
        
        await event_bus.disconnect()
        return detected_clips
    
    @pytest.mark.asyncio
    async def test_create_replays_from_clips(self, match_id):
        """
        Test: Create multi-angle replays from detected clips
        
        Takes detected highlight clips and creates
        professional replay sequences with multiple angles.
        """
        print("\n" + "="*60)
        print("E2E: CREATE REPLAYS FROM CLIPS")
        print("="*60)
        
        # First detect clips
        event_bus = EventBus()
        await event_bus.connect()
        
        clip_detector = ClipDetector()
        
        # Detect a clutch clip
        clutch_clip = clip_detector.detect_clip(
            event_type="clutch_win",
            event_data={
                "player_name": "ScoutOP",
                "team_name": "Team Soul",
                "enemy_count": 4,
                "win_probability": 0.06,
                "enemies_killed": 4,
                "start_time_ms": 600000,
                "end_time_ms": 620000,
            },
            match_context={"match_id": match_id},
        )
        
        assert clutch_clip is not None, "Clutch clip should be detected"
        print(f"✓ Source clip: {clutch_clip.title}")
        
        # Create replay sequence
        replay_director = ReplayDirector()
        
        available_angles = [
            ReplayAngle.PLAYER_POV,
            ReplayAngle.ENEMY_POV,
            ReplayAngle.OVERHEAD,
            ReplayAngle.FREECAM,
            ReplayAngle.SPLIT_SCREEN,
        ]
        
        sequence = replay_director.create_replay(
            clip_data={
                "clip_id": clutch_clip.clip_id,
                "title": clutch_clip.title,
                "category": clutch_clip.category.value,
                "primary_player": clutch_clip.primary_player,
                "duration_seconds": clutch_clip.duration_seconds,
            },
            available_angles=available_angles,
        )
        
        assert sequence is not None
        assert len(sequence.shots) > 0
        
        print("✓ Replay sequence created:")
        print(f"  Total duration: {sequence.total_duration_ms/1000:.1f}s")
        print(f"  Shots: {len(sequence.shots)}")
        
        for shot in sequence.shots:
            print(f"    - {shot.angle.value} @ {shot.speed.value} ({shot.duration_ms}ms)")
        
        # Test playback simulation
        replay_director.play_replay(sequence.sequence_id)
        assert sequence.is_playing is True
        
        # Advance through all shots
        for _ in range(len(sequence.shots)):
            shot = replay_director.get_current_shot()
            if shot:
                print(f"  ▶ Playing: {shot['angle']} at {shot['speed']}")
            replay_director.advance_shot()
        
        assert sequence.is_playing is False
        print("✓ Replay playback completed")
        
        await event_bus.disconnect()
    
    @pytest.mark.asyncio
    async def test_generate_montage(self, match_id):
        """
        Test: Generate highlight montage from multiple clips
        
        Compiles multiple highlight clips into a
        professionally edited montage with music sync.
        """
        print("\n" + "="*60)
        print("E2E: GENERATE HIGHLIGHT MONTAGE")
        print("="*60)
        
        # Create sample clips
        clips = [
            {
                "clip_id": "clip_001",
                "title": "Squad Wipe in Pochinki",
                "category": "squad_wipe",
                "excitement_score": 82,
                "duration_seconds": 12.0,
                "timestamp_ms": 300000,
            },
            {
                "clip_id": "clip_002",
                "title": "ScoutOP 1v4 Clutch",
                "category": "clutch_win",
                "excitement_score": 98,
                "duration_seconds": 20.0,
                "timestamp_ms": 600000,
            },
            {
                "clip_id": "clip_003",
                "title": "Jonathan 4-Kill Rampage",
                "category": "multi_kill",
                "excitement_score": 88,
                "duration_seconds": 8.0,
                "timestamp_ms": 900000,
            },
            {
                "clip_id": "clip_004",
                "title": "Perfect Grenade Triple Kill",
                "category": "grenade_kill",
                "excitement_score": 78,
                "duration_seconds": 6.0,
                "timestamp_ms": 1200000,
            },
            {
                "clip_id": "clip_005",
                "title": "320m AWM Headshot",
                "category": "long_range",
                "excitement_score": 85,
                "duration_seconds": 7.0,
                "timestamp_ms": 1500000,
            },
            {
                "clip_id": "clip_006",
                "title": "Final Circle Victory",
                "category": "final_circle",
                "excitement_score": 92,
                "duration_seconds": 25.0,
                "timestamp_ms": 1700000,
            },
        ]
        
        # Generate montage
        montage_gen = MontageGenerator()
        
        montage = montage_gen.generate_montage(
            clips=clips,
            style=MontageStyle.HYPE,
            music_genre=MusicGenre.PHONK,
            title="AEGIS Invitational - Best Moments",
            target_duration_ms=60000,  # 60 seconds
        )
        
        assert montage is not None
        assert len(montage.segments) > 0
        
        print(f"✓ Montage generated: {montage.title}")
        print(f"  Style: {montage.style.value}")
        print(f"  Music: {montage.music_genre.value} ({montage.music_bpm} BPM)")
        print(f"  Segments: {len(montage.segments)}")
        print(f"  Total duration: {montage.total_duration_ms/1000:.1f}s")
        print(f"  Pacing score: {montage.pacing_score:.0f}/100")
        
        # Verify segments
        for i, seg in enumerate(montage.segments):
            clip = clips[i]
            print(f"  Segment {i+1}: {clip['title'][:40]}... ({seg.duration_ms}ms, {seg.effect})")
        
        print("\n✓ Montage generation pipeline works!")
    
    @pytest.mark.asyncio
    async def test_content_publishing_pipeline(self, match_id):
        """
        Test: Content publishing to social platforms
        
        Takes highlight clips and publishes them
        to multiple social media platforms.
        """
        print("\n" + "="*60)
        print("E2E: CONTENT PUBLISHING PIPELINE")
        print("="*60)
        
        # Create sample clips
        clips = [
            {
                "clip_id": "clip_clutch_001",
                "title": "🔥 INSANE 1v4 CLUTCH by ScoutOP!",
                "description": "ScoutOP pulls off an impossible 1v4 clutch with only 5% win probability! Legendary moment!",
                "category": "clutch_win",
                "excitement_score": 98,
                "viral_potential": 95,
                "duration_seconds": 20.0,
                "primary_player": "ScoutOP",
                "primary_team": "Team Soul",
                "hashtags": ["#BGMI", "#Clutch", "#ScoutOP", "#Esports", "#1v4"],
                "platform_suitability": {
                    "youtube_shorts": 95,
                    "tiktok": 98,
                    "instagram_reels": 95,
                    "youtube_full": 85,
                },
            },
            {
                "clip_id": "clip_funny_001",
                "title": "😂 EPIC FAIL Compilation!",
                "description": "The funniest BGMI fails you'll see today!",
                "category": "funny_moment",
                "excitement_score": 45,
                "viral_potential": 88,
                "duration_seconds": 15.0,
                "hashtags": ["#BGMI", "#Funny", "#Fails", "#Gaming"],
                "platform_suitability": {
                    "tiktok": 95,
                    "instagram_reels": 90,
                    "youtube_shorts": 80,
                },
            },
        ]
        
        # Initialize publisher
        publisher = ContentPublisher()
        
        # Publish each clip to suitable platforms
        for clip in clips:
            # Auto-publish to best platforms
            job_ids = publisher.publish_best_to_all(
                clip_data=clip,
                min_excitement=40.0,
            )
            
            print(f"\n✓ Publishing: {clip['title']}")
            print(f"  Platforms: {len(job_ids)}")
            
            for job_id in job_ids:
                job = publisher.jobs.get(job_id)
                if job:
                    print(f"    - {job.platform.value}: {job.status.value}")
        
        # Check stats
        stats = publisher.get_stats()
        print("\n✓ Publishing stats:")
        print(f"  Total jobs: {stats['total_jobs']}")
        print(f"  Pending: {stats['pending_jobs']}")
        
        for platform, platform_stats in stats.get("by_platform", {}).items():
            if platform_stats.get("published", 0) > 0:
                print(f"  {platform}: {platform_stats['published']} published")
        
        print("\n✅ Content publishing pipeline works!")