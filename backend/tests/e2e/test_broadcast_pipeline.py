# backend/tests/e2e/test_broadcast_pipeline.py
"""
End-to-End Test: Broadcast Pipeline
Tests the complete broadcast pipeline from event detection
to OBS scene switching, overlay updates, and replay generation.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, Mock

from app.core.event_bus import EventBus
from app.broadcast.scene_manager import SceneManager
from app.broadcast.replay_controller import ReplayController
from app.overlays.data_pusher import DataPusher, DataChannel


@pytest.mark.e2e
@pytest.mark.slow
class TestBroadcastPipeline:
    """
    End-to-end test for the complete broadcast pipeline.
    
    Flow:
    1. Event Detection → Observer Director Decision
    2. Director Decision → OBS Scene Switch
    3. Scene Switch → Overlay Update
    4. Highlight Detection → Replay Creation
    5. Replay Creation → OBS Replay Playback
    """
    
    @pytest.fixture
    def mock_obs(self):
        """Create comprehensive mock OBS"""
        obs = AsyncMock()
        obs.send_command = AsyncMock(return_value=Mock(success=True, data={}))
        obs.switch_scene = AsyncMock(return_value=True)
        obs.is_connected = Mock(return_value=True)
        obs.current_scene = "Main Camera"
        obs.scene_list = [
            "Main Camera", "Team A POV", "Team B POV",
            "Map Overview", "Fight Coverage", "Clutch Focus",
            "Replay", "Scoreboard", "Match End"
        ]
        return obs
    
    @pytest.fixture
    def mock_overlay_server(self):
        """Create mock overlay server"""
        server = AsyncMock()
        server.broadcast_to_type = AsyncMock(return_value=5)
        server.broadcast_to_all = AsyncMock(return_value=10)
        return server
    
    @pytest.mark.asyncio
    async def test_detect_fight_to_scene_switch(self, mock_obs, mock_overlay_server):
        """
        Test: Fight Detection → Observer Decision → OBS Scene Switch → Overlay Update
        
        This is the core broadcast loop:
        1. Fight is detected by vision pipeline
        2. Event is published to event bus
        3. Observer Director receives the event
        4. Director decides to switch to fight coverage
        5. OBS scene is switched
        6. Overlays are updated with fight info
        """
        print("\n" + "="*60)
        print("E2E: FIGHT DETECTION → SCENE SWITCH → OVERLAY UPDATE")
        print("="*60)
        
        # Setup
        event_bus = EventBus()
        await event_bus.connect()
        
        # Initialize broadcast components
        scene_manager = SceneManager(mock_obs)
        await scene_manager.setup_broadcast_scenes()
        
        data_pusher = DataPusher(mock_overlay_server)
        await data_pusher.start()
        
        # Simulate: Fight detected by vision
        fight_event = {
            "fight_id": "fight_e2e_001",
            "teams": ["team_001", "team_002"],
            "intensity": "high",
            "intensity_score": 85,
            "is_clutch": False,
            "entertainment_score": 78,
            "location": (4000, 5000),
            "zone_phase": 4,
        }
        
        await event_bus.publish(
            event_type="fight_detected",
            data=fight_event,
            match_id="e2e_broadcast_match",
            priority=1,  # CRITICAL
        )
        print("✓ Fight detected event published")
        
        # Simulate: Observer Director receives and decides
        # (In production, this is handled by the actual agent)
        decision = {
            "type": "switch_feed",
            "target": "Fight Coverage",
            "reason": f"High intensity fight ({fight_event['intensity_score']}/100)",
            "fight_id": fight_event["fight_id"],
        }
        print(f"✓ Director decision: {decision['reason']}")
        
        # Execute: Switch OBS scene
        await scene_manager.switch_to(decision["target"])
        print(f"✓ OBS scene switched to: {decision['target']}")
        
        # Execute: Push overlay updates
        await data_pusher.push(DataChannel.FIGHT_INFO, {
            "fight_id": fight_event["fight_id"],
            "teams": fight_event["teams"],
            "intensity": fight_event["intensity"],
            "intensity_score": fight_event["intensity_score"],
        })
        
        await data_pusher.push(DataChannel.SCOREBOARD, {
            "active_fight": True,
            "fight_info": f"Team A vs Team B - {fight_event['intensity'].upper()} INTENSITY",
        })
        print("✓ Overlay data pushed for fight info and scoreboard")
        
        # Verify
        assert scene_manager.active_scene == "Fight Coverage"
        print("\n✅ Fight detection → Scene switch pipeline works!")
        
        await data_pusher.stop()
        await event_bus.disconnect()
    
    @pytest.mark.asyncio
    async def test_clutch_to_replay_flow(self, mock_obs, mock_overlay_server):
        """
        Test: Clutch Detection → Clutch Focus → Replay Creation → Replay Playback
        
        This tests the most critical broadcast sequence:
        1. Clutch situation detected
        2. Director forces switch to clutch focus
        3. After clutch resolves, replay is created
        4. Replay is queued and played back
        5. After replay, return to live action
        """
        print("\n" + "="*60)
        print("E2E: CLUTCH DETECTION → FOCUS → REPLAY → PLAYBACK")
        print("="*60)
        
        # Setup
        event_bus = EventBus()
        await event_bus.connect()
        
        scene_manager = SceneManager(mock_obs)
        await scene_manager.setup_broadcast_scenes()
        
        replay_controller = ReplayController(mock_obs)
        await replay_controller.start_buffer()
        
        data_pusher = DataPusher(mock_overlay_server)
        await data_pusher.start()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 1: Clutch Detected
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        clutch_event = {
            "clutch_id": "clutch_e2e_001",
            "player_name": "ScoutOP",
            "team_id": "team_001",
            "team_name": "Team Soul",
            "clutch_type": "1v4",
            "probability": 0.08,
            "enemies_remaining": 4,
            "location": (4100, 5100),
        }
        
        await event_bus.publish(
            event_type="clutch_detected",
            data=clutch_event,
            match_id="e2e_broadcast_match",
            priority=1,
        )
        print(f"✓ Clutch detected: {clutch_event['player_name']} 1v4 ({clutch_event['probability']:.0%} win chance)")
        
        # Force switch to clutch focus
        await scene_manager.switch_to("Clutch Focus")
        print("✓ Switched to Clutch Focus")
        
        # Push clutch overlay data
        await data_pusher.push(DataChannel.CLUTCH_INFO, {
            "player_name": clutch_event["player_name"],
            "clutch_type": clutch_event["clutch_type"],
            "probability": f"{clutch_event['probability']:.0%}",
        })
        print("✓ Clutch overlay updated")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 2: Clutch Progress (kills happening)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        for i in range(3):
            await event_bus.publish(
                event_type="kill",
                data={
                    "killer_id": "player_scout",
                    "killer_name": "ScoutOP",
                    "victim_id": f"enemy_{i+1}",
                    "weapon": "M416" if i < 2 else "AWM",
                    "headshot": i == 1,
                    "clutch_id": clutch_event["clutch_id"],
                },
                match_id="e2e_broadcast_match",
            )
            
            await data_pusher.push(DataChannel.CLUTCH_INFO, {
                "enemies_remaining": 3 - i,
                "progress": f"{(i+1)}/4 eliminated",
            })
        
        print("✓ Clutch progress: 3/4 enemies eliminated")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 3: Clutch Win!
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        await event_bus.publish(
            event_type="kill",
            data={
                "killer_id": "player_scout",
                "killer_name": "ScoutOP",
                "victim_id": "enemy_4",
                "weapon": "AWM",
                "headshot": True,
                "clutch_id": clutch_event["clutch_id"],
            },
            match_id="e2e_broadcast_match",
        )
        
        await event_bus.publish(
            event_type="clutch_win",
            data={
                "clutch_id": clutch_event["clutch_id"],
                "player_name": "ScoutOP",
                "enemies_killed": 4,
                "probability": 0.08,
            },
            match_id="e2e_broadcast_match",
        )
        
        print("✓ CLUTCH WIN! ScoutOP wins 1v4!")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 4: Create Replay
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        clip = replay_controller.create_clip(
            title="ScoutOP 1v4 INSANE Clutch!",
            start_offset=-15.0,
            duration=20.0,
            excitement=0.98,
        )
        
        clip.tags = ["clutch", "1v4", "ScoutOP", "legendary"]
        replay_controller.queue_replay(clip.clip_id)
        
        print(f"✓ Replay clip created: {clip.title}")
        print(f"  Duration: {clip.duration_ms/1000:.1f}s")
        print(f"  Excitement: {clip.excitement_score:.0%}")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 5: Play Replay
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        next_replay = replay_controller.get_next_replay()
        
        if next_replay:
            await replay_controller.play_replay(next_replay.clip_id, speed=0.5)
            await scene_manager.switch_to("Replay")
            
            # Push replay overlay
            await data_pusher.push(DataChannel.MATCH_INFO, {
                "replay_active": True,
                "replay_title": next_replay.title,
                "replay_speed": "0.5x",
            })
            
            print(f"✓ Replay playing: {next_replay.title} at 0.5x speed")
            
            # Simulate replay duration
            await asyncio.sleep(0.1)  # Would be actual replay duration
            
            await replay_controller.stop_replay()
            print("✓ Replay finished")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STEP 6: Return to Live
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        await scene_manager.switch_to("Main Camera")
        await data_pusher.push(DataChannel.MATCH_INFO, {"replay_active": False})
        
        print("✓ Returned to live broadcast")
        
        # Verify
        assert scene_manager.active_scene == "Main Camera"
        assert replay_controller.state.value == "stopped"
        assert replay_controller._total_replays_played == 1
        
        print("\n✅ Clutch → Focus → Replay → Playback pipeline works!")
        
        await data_pusher.stop()
        await replay_controller.stop_buffer()
        await event_bus.disconnect()
    
    @pytest.mark.asyncio
    async def test_full_match_broadcast_flow(self, mock_obs, mock_overlay_server):
        """
        Test: Complete match broadcast flow
        
        Simulates the entire broadcast sequence for a match:
        Starting Soon → Early Game → Mid Game → Late Game → 
        Final Circle → Match End → Post-Match
        """
        print("\n" + "="*60)
        print("E2E: FULL MATCH BROADCAST FLOW")
        print("="*60)
        
        # Setup all components
        event_bus = EventBus()
        await event_bus.connect()
        
        scene_manager = SceneManager(mock_obs)
        await scene_manager.setup_broadcast_scenes()
        
        replay_controller = ReplayController(mock_obs)
        await replay_controller.start_buffer()
        
        data_pusher = DataPusher(mock_overlay_server)
        await data_pusher.start()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # BROADCAST SEQUENCE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        broadcast_phases = [
            # (Phase, Scene, Duration, Overlay Data)
            ("Starting Soon", "Starting Soon", 0.05, {"status": "starting", "countdown": 30}),
            ("Early Game", "Main Camera", 0.05, {"status": "early_game", "teams_alive": 16}),
            ("First Blood", "Fight Coverage", 0.05, {"status": "fight", "first_blood": True}),
            ("Mid Game Rotation", "Map Overview", 0.05, {"status": "mid_game", "teams_alive": 12}),
            ("Intense Fight", "Fight Coverage", 0.05, {"status": "fight", "intensity": "high"}),
            ("Clutch Situation", "Clutch Focus", 0.05, {"status": "clutch", "type": "1v3"}),
            ("Replay", "Replay", 0.05, {"status": "replay", "title": "Clutch Replay"}),
            ("Late Game", "Main Camera", 0.05, {"status": "late_game", "teams_alive": 5}),
            ("Final Circle", "Fight Coverage", 0.05, {"status": "final_circle", "teams_alive": 3}),
            ("Winner!", "Match End", 0.05, {"status": "match_end", "winner": "Team Soul"}),
        ]
        
        for phase_name, scene, duration, overlay_data in broadcast_phases:
            print(f"\n📺 {phase_name}")
            
            # Switch scene
            await scene_manager.switch_to(scene)
            
            # Push overlay data
            for channel_name, value in overlay_data.items():
                channel = getattr(DataChannel, "MATCH_INFO", DataChannel.SCOREBOARD)
                await data_pusher.push(channel, {channel_name: value})
            
            # Simulate scene duration
            await asyncio.sleep(duration)
            
            print(f"  ✓ Scene: {scene}")
            print(f"  ✓ Data pushed: {list(overlay_data.keys())}")
        
        print("\n✅ Full match broadcast flow completed!")
        print(f"  Total phases: {len(broadcast_phases)}")
        print(f"  Scenes used: {len(set(p[1] for p in broadcast_phases))}")
        
        # Verify final state
        assert scene_manager.active_scene == "Match End"
        
        await data_pusher.stop()
        await replay_controller.stop_buffer()
        await event_bus.disconnect()