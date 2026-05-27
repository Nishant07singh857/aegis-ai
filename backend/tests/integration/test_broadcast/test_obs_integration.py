# backend/tests/integration/test_broadcast/test_obs_integration.py
"""
Integration tests for OBS Integration Layer.
Tests OBS WebSocket client, scene management, source control,
transitions, audio mixing, replay system, and recording management.
"""

import pytest
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from app.broadcast.obs_client import OBSClient, OBSConfig, OBSResponse, ConnectionState
from app.broadcast.scene_manager import SceneManager, Scene, SceneItem, SceneType
from app.broadcast.source_manager import SourceManager
from app.broadcast.transition_controller import (
    TransitionController
)
from app.broadcast.audio_mixer import AudioMixer, AudioChannel
from app.broadcast.replay_controller import (
    ReplayController, ReplayState
)
from app.broadcast.recording_manager import (
    RecordingManager, RecordingStatus
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OBS CLIENT TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestOBSClient:
    """Test OBS WebSocket client"""
    
    @pytest.fixture
    def obs_config(self):
        """Create test OBS config"""
        return OBSConfig(
            host="localhost",
            port=4455,
            password="test_password",
            reconnect_attempts=3,
            reconnect_delay=1.0,
            reconnect_backoff=1.5,
            connection_timeout=5.0,
            heartbeat_interval=5.0,
            command_timeout=3.0,
            max_retries=2,
            subscribe_to_events=True,
            event_buffer_size=50,
        )
    
    @pytest.fixture
    def obs_client(self, obs_config):
        """Create OBS client instance"""
        return OBSClient(obs_config)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_initial_state(self, obs_client):
        """Test client initial state"""
        assert obs_client.state == ConnectionState.DISCONNECTED
        assert obs_client._request_id == 0
        assert len(obs_client._pending_requests) == 0
        assert len(obs_client._event_handlers) == 0
        assert obs_client.current_scene == ""
        assert obs_client.streaming is False
        assert obs_client.recording is False
    
    def test_config_values(self, obs_client, obs_config):
        """Test config values are set correctly"""
        assert obs_client.config.host == "localhost"
        assert obs_client.config.port == 4455
        assert obs_client.config.password == "test_password"
        assert obs_client.config.reconnect_attempts == 3
        assert obs_client.config.command_timeout == 3.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMAND EXECUTION TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_send_command_success(self, obs_client):
        """Test sending a successful command"""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7,
            "d": {
                "requestId": "cmd_1",
                "requestStatus": {
                    "result": True,
                    "responseData": {"sceneName": "TestScene", "sceneIndex": 0}
                }
            }
        }))
        
        obs_client._ws = mock_ws
        obs_client.state = ConnectionState.CONNECTED
        
        response = await obs_client.send_command("GetCurrentProgramScene")
        
        assert response.success is True
        assert response.data == {"sceneName": "TestScene", "sceneIndex": 0}
        assert response.request_id == "cmd_1"
        assert response.latency_ms > 0
        mock_ws.send.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_send_command_with_params(self, obs_client):
        """Test sending command with parameters"""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7,
            "d": {
                "requestId": "cmd_1",
                "requestStatus": {"result": True, "responseData": {}}
            }
        }))
        
        obs_client._ws = mock_ws
        obs_client.state = ConnectionState.CONNECTED
        
        response = await obs_client.send_command(
            "SetCurrentProgramScene",
            params={"sceneName": "Main Camera"}
        )
        
        assert response.success is True
        # Verify correct message was sent
        sent_message = json.loads(mock_ws.send.call_args[0][0])
        assert sent_message["d"]["requestType"] == "SetCurrentProgramScene"
        assert sent_message["d"]["requestData"]["sceneName"] == "Main Camera"
    
    @pytest.mark.asyncio
    async def test_send_command_failure(self, obs_client):
        """Test handling command failure response"""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7,
            "d": {
                "requestId": "cmd_1",
                "requestStatus": {
                    "result": False,
                    "comment": "Scene not found",
                    "code": 404
                }
            }
        }))
        
        obs_client._ws = mock_ws
        obs_client.state = ConnectionState.CONNECTED
        
        response = await obs_client.send_command("SetCurrentProgramScene")
        
        assert response.success is False
        assert "Scene not found" in (response.error or "")
    
    @pytest.mark.asyncio
    async def test_send_command_not_connected(self, obs_client):
        """Test sending command when not connected"""
        response = await obs_client.send_command("GetVersion")
        
        assert response.success is False
        assert "Not connected" in (response.error or "")
    
    @pytest.mark.asyncio
    async def test_send_command_timeout(self, obs_client):
        """Test command timeout handling"""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock()
        mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError())
        
        obs_client._ws = mock_ws
        obs_client.state = ConnectionState.CONNECTED
        
        response = await obs_client.send_command("GetVersion", timeout=0.05)
        
        assert response.success is False
        assert "timed out" in (response.error or "").lower()
    
    @pytest.mark.asyncio
    async def test_send_command_connection_error(self, obs_client):
        """Test command with connection error"""
        mock_ws = AsyncMock()
        mock_ws.send = AsyncMock(side_effect=ConnectionError("Connection lost"))
        
        obs_client._ws = mock_ws
        obs_client.state = ConnectionState.CONNECTED
        
        response = await obs_client.send_command("GetVersion")
        
        assert response.success is False
        assert "Connection lost" in (response.error or "")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HIGH-LEVEL COMMAND TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_switch_scene(self, obs_client):
        """Test switching scene"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        result = await obs_client.switch_scene("Main Camera")
        
        assert result is True
        assert obs_client.current_scene == "Main Camera"
    
    @pytest.mark.asyncio
    async def test_start_stop_streaming(self, obs_client):
        """Test start/stop streaming"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        # Start
        result = await obs_client.start_streaming()
        assert result is True
        assert obs_client.streaming is True
        
        # Stop
        result = await obs_client.stop_streaming()
        assert result is True
        assert obs_client.streaming is False
    
    @pytest.mark.asyncio
    async def test_start_stop_recording(self, obs_client):
        """Test start/stop recording"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        result = await obs_client.start_recording()
        assert result is True
        assert obs_client.recording is True
        
        result = await obs_client.stop_recording()
        assert result is True
        assert obs_client.recording is False
    
    @pytest.mark.asyncio
    async def test_set_source_visibility(self, obs_client):
        """Test setting source visibility"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        result = await obs_client.set_source_visibility("Main Camera", "GameSource", True)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_set_volume(self, obs_client):
        """Test setting source volume"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        result = await obs_client.set_volume("GameAudio", 0.75)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_set_mute(self, obs_client):
        """Test muting/unmuting source"""
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        obs_client._ws.recv = AsyncMock(return_value=json.dumps({
            "op": 7, "d": {"requestId": "cmd_1", "requestStatus": {"result": True}}
        }))
        obs_client.state = ConnectionState.CONNECTED
        
        result = await obs_client.set_mute("MicSource", True)
        assert result is True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HANDLING TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_on_event_registration(self, obs_client):
        """Test registering event handlers"""
        handler_called = []
        
        def handler(data):
            handler_called.append(data)
        
        obs_client.on_event("SceneChanged", handler)
        
        assert "SceneChanged" in obs_client._event_handlers
        assert len(obs_client._event_handlers["SceneChanged"]) == 1
    
    @pytest.mark.asyncio
    async def test_handle_event_dispatch(self, obs_client):
        """Test event dispatch to handlers"""
        handler_data = []
        
        async def async_handler(data):
            handler_data.append(data)
        
        obs_client.on_event("SceneChanged", async_handler)
        
        event_data = {
            "eventType": "SceneChanged",
            "eventData": {"sceneName": "Main Camera"}
        }
        
        await obs_client._handle_event(event_data)
        
        assert len(handler_data) == 1
        assert handler_data[0] == {"sceneName": "Main Camera"}
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUEUE TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    @pytest.mark.asyncio
    async def test_queue_command(self, obs_client):
        """Test queuing commands"""
        obs_client.state = ConnectionState.CONNECTED
        obs_client._ws = AsyncMock()
        obs_client._ws.send = AsyncMock()
        
        await obs_client.queue_command("GetVersion")
        await obs_client.queue_command("GetSceneList")
        
        assert obs_client._command_queue.qsize() == 2
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS TESTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def test_is_connected(self, obs_client):
        """Test connection status check"""
        assert obs_client.is_connected() is False
        
        obs_client.state = ConnectionState.CONNECTED
        assert obs_client.is_connected() is True
        
        obs_client.state = ConnectionState.RECONNECTING
        assert obs_client.is_connected() is False
    
    def test_get_stats(self, obs_client):
        """Test getting client statistics"""
        obs_client._commands_sent = 50
        obs_client._commands_failed = 5
        obs_client._events_received = 200
        
        stats = obs_client.get_stats()
        
        assert stats["state"] == "disconnected"
        assert stats["connected"] is False
        assert stats["commands_sent"] == 50
        assert stats["commands_failed"] == 5
        assert stats["events_received"] == 200


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SCENE MANAGER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestSceneManager:
    """Test Scene Manager"""
    
    @pytest.fixture
    def mock_obs(self):
        """Create mock OBS client"""
        obs = AsyncMock()
        obs.send_command = AsyncMock(return_value=OBSResponse(
            success=True, data={}, request_id="test", latency_ms=1.0
        ))
        obs.switch_scene = AsyncMock(return_value=True)
        obs.scene_list = [
            "Main Camera", "Team A POV", "Team B POV",
            "Map Overview", "Replay", "Scoreboard"
        ]
        obs.set_source_visibility = AsyncMock(return_value=True)
        return obs
    
    @pytest.fixture
    def scene_manager(self, mock_obs):
        """Create scene manager"""
        return SceneManager(mock_obs)
    
    @pytest.mark.asyncio
    async def test_create_scene(self, scene_manager):
        """Test creating a scene"""
        result = await scene_manager.create_scene("TestScene", SceneType.MAIN_CAMERA)
        
        assert result is True
        assert "TestScene" in scene_manager.scenes
        assert scene_manager.scenes["TestScene"].scene_type == SceneType.MAIN_CAMERA
    
    @pytest.mark.asyncio
    async def test_create_scene_obs_failure(self, scene_manager, mock_obs):
        """Test scene creation when OBS fails"""
        mock_obs.send_command = AsyncMock(return_value=OBSResponse(
            success=False, error="Scene already exists"
        ))
        
        result = await scene_manager.create_scene("ExistingScene")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_remove_scene(self, scene_manager):
        """Test removing a scene"""
        await scene_manager.create_scene("ToRemove")
        
        result = await scene_manager.remove_scene("ToRemove")
        
        assert result is True
        assert "ToRemove" not in scene_manager.scenes
    
    @pytest.mark.asyncio
    async def test_switch_to_scene(self, scene_manager):
        """Test switching to a scene"""
        scene_manager.scenes["TestScene"] = Scene(name="TestScene")
        
        result = await scene_manager.switch_to("TestScene")
        
        assert result is True
        assert scene_manager.active_scene == "TestScene"
    
    @pytest.mark.asyncio
    async def test_switch_to_scene_with_transition(self, scene_manager):
        """Test switching with custom transition"""
        scene_manager.scenes["TestScene"] = Scene(
            name="TestScene",
            default_transition="fade",
            transition_duration_ms=500,
        )
        
        result = await scene_manager.switch_to("TestScene", transition="wipe")
        
        assert result is True
    
    @pytest.mark.asyncio
    async def test_switch_to_nonexistent_scene(self, scene_manager):
        """Test switching to non-existent scene"""
        result = await scene_manager.switch_to("NonExistent")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_switch_back(self, scene_manager):
        """Test switching back to previous scene"""
        scene_manager.scenes["Scene1"] = Scene(name="Scene1")
        scene_manager.scenes["Scene2"] = Scene(name="Scene2")
        
        await scene_manager.switch_to("Scene1")
        await scene_manager.switch_to("Scene2")
        
        result = await scene_manager.switch_back()
        
        assert result is True
        assert scene_manager.active_scene == "Scene1"
    
    @pytest.mark.asyncio
    async def test_switch_back_no_history(self, scene_manager):
        """Test switching back with no history"""
        result = await scene_manager.switch_back()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_add_source_to_scene(self, scene_manager):
        """Test adding source to scene"""
        scene_manager.scenes["TestScene"] = Scene(name="TestScene")
        
        result = await scene_manager.add_source_to_scene(
            "TestScene", "GameSource", visible=True
        )
        
        assert result is True
        assert len(scene_manager.scenes["TestScene"].items) == 1
    
    @pytest.mark.asyncio
    async def test_remove_source_from_scene(self, scene_manager):
        """Test removing source from scene"""
        scene_manager.scenes["TestScene"] = Scene(name="TestScene")
        await scene_manager.add_source_to_scene("TestScene", "GameSource")
        
        # Need to set item_id
        scene_manager.scenes["TestScene"].items[0].item_id = 1
        
        result = await scene_manager.remove_source_from_scene("TestScene", "GameSource")
        
        assert result is True
        assert len(scene_manager.scenes["TestScene"].items) == 0
    
    @pytest.mark.asyncio
    async def test_set_item_visible(self, scene_manager):
        """Test toggling item visibility"""
        result = await scene_manager.set_item_visible("Scene", "Source", True)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_set_item_position(self, scene_manager):
        """Test setting item position"""
        scene_manager.scenes["TestScene"] = Scene(name="TestScene")
        scene_manager.scenes["TestScene"].items.append(
            SceneItem(item_id=1, name="Source1")
        )
        
        result = await scene_manager.set_item_position("TestScene", "Source1", 100, 200)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_setup_broadcast_scenes(self, scene_manager):
        """Test setting up all broadcast scenes"""
        result = await scene_manager.setup_broadcast_scenes()
        
        assert result is True
        assert len(scene_manager.scenes) >= 10
    
    def test_get_stats(self, scene_manager):
        """Test getting scene manager statistics"""
        scene_manager.scenes["Scene1"] = Scene(name="Scene1", scene_type=SceneType.MAIN_CAMERA)
        scene_manager.scenes["Scene2"] = Scene(name="Scene2", scene_type=SceneType.REPLAY)
        scene_manager.active_scene = "Scene1"
        
        stats = scene_manager.get_stats()
        
        assert stats["active_scene"] == "Scene1"
        assert stats["scene_count"] == 2
        assert len(stats["scenes"]) == 2


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# REPLAY CONTROLLER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestReplayController:
    """Test Replay Controller"""
    
    @pytest.fixture
    def mock_obs(self):
        """Create mock OBS client"""
        obs = AsyncMock()
        obs.send_command = AsyncMock(return_value=OBSResponse(success=True, data={}))
        obs.switch_scene = AsyncMock(return_value=True)
        return obs
    
    @pytest.fixture
    def replay_controller(self, mock_obs):
        """Create replay controller"""
        return ReplayController(mock_obs)
    
    @pytest.mark.asyncio
    async def test_start_buffer(self, replay_controller):
        """Test starting replay buffer"""
        result = await replay_controller.start_buffer()
        
        assert result is True
        assert replay_controller.state == ReplayState.RECORDING
        assert replay_controller.replay_buffer_start > 0
    
    @pytest.mark.asyncio
    async def test_stop_buffer(self, replay_controller):
        """Test stopping replay buffer"""
        replay_controller.state = ReplayState.RECORDING
        
        result = await replay_controller.stop_buffer()
        
        assert result is True
        assert replay_controller.state == ReplayState.STOPPED
    
    @pytest.mark.asyncio
    async def test_save_buffer(self, replay_controller):
        """Test saving replay buffer"""
        result = await replay_controller.save_buffer()
        
        assert result is not None
        assert result.startswith("replay_")
    
    def test_create_clip(self, replay_controller):
        """Test creating a replay clip"""
        clip = replay_controller.create_clip(
            title="Epic Squad Wipe",
            start_offset=-8.0,
            duration=12.0,
            excitement=0.88,
        )
        
        assert clip is not None
        assert clip.title == "Epic Squad Wipe"
        assert clip.duration_ms == 12000
        assert clip.excitement_score == 0.88
        assert clip.replay_value == 0.88
        assert clip.played is False
        assert clip.play_count == 0
    
    def test_create_clip_auto_queue(self, replay_controller):
        """Test clip auto-queuing for exciting moments"""
        clip = replay_controller.create_clip(
            title="Super Exciting Moment",
            excitement=0.85,  # Above 0.6 threshold
        )
        
        assert clip.clip_id in replay_controller.replay_queue
    
    def test_create_clip_not_queued_low_excitement(self, replay_controller):
        """Test clip not queued for low excitement"""
        clip = replay_controller.create_clip(
            title="Boring Moment",
            excitement=0.3,
        )
        
        assert clip.clip_id not in replay_controller.replay_queue
    
    def test_queue_replay(self, replay_controller):
        """Test queuing a replay"""
        clip = replay_controller.create_clip("Test Clip", excitement=0.7)
        
        # Manually queue (even though auto-queued)
        result = replay_controller.queue_replay(clip.clip_id)
        
        assert result is True
    
    def test_queue_replay_invalid(self, replay_controller):
        """Test queuing invalid replay"""
        result = replay_controller.queue_replay("invalid_clip_id")
        assert result is False
    
    def test_get_next_replay(self, replay_controller):
        """Test getting next replay from queue"""
        clip = replay_controller.create_clip("Clip 1", excitement=0.7)
        replay_controller.queue_replay(clip.clip_id)
        
        next_clip = replay_controller.get_next_replay()
        
        assert next_clip is not None
        assert next_clip.title == "Clip 1"
        assert len(replay_controller.replay_queue) == 0
    
    def test_get_next_replay_empty(self, replay_controller):
        """Test getting next replay from empty queue"""
        next_clip = replay_controller.get_next_replay()
        assert next_clip is None
    
    @pytest.mark.asyncio
    async def test_play_replay(self, replay_controller):
        """Test playing a replay"""
        clip = replay_controller.create_clip("Test Clip", excitement=0.7)
        
        result = await replay_controller.play_replay(clip.clip_id, speed=0.5)
        
        assert result is True
        assert replay_controller.state == ReplayState.PLAYING
        assert replay_controller.active_clip is not None
        assert replay_controller.playback_speed == 0.5
        assert clip.played is True
        assert clip.play_count == 1
    
    @pytest.mark.asyncio
    async def test_play_replay_invalid(self, replay_controller):
        """Test playing invalid replay"""
        result = await replay_controller.play_replay("invalid_id")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_stop_replay(self, replay_controller):
        """Test stopping replay"""
        clip = replay_controller.create_clip("Test Clip")
        await replay_controller.play_replay(clip.clip_id)
        
        result = await replay_controller.stop_replay()
        
        assert result is True
        assert replay_controller.state == ReplayState.STOPPED
        assert replay_controller.active_clip is None
    
    @pytest.mark.asyncio
    async def test_set_speed(self, replay_controller):
        """Test setting replay playback speed"""
        clip = replay_controller.create_clip("Test Clip")
        await replay_controller.play_replay(clip.clip_id)
        
        # Valid speeds
        result = await replay_controller.set_speed(0.25)
        assert result is True
        assert replay_controller.playback_speed == 0.25
        
        result = await replay_controller.set_speed(2.0)
        assert result is True
        assert replay_controller.playback_speed == 2.0
        
        # Invalid speeds should be clamped
        result = await replay_controller.set_speed(0.1)
        assert replay_controller.playback_speed == 0.25  # Clamped to min
        
        result = await replay_controller.set_speed(3.0)
        assert replay_controller.playback_speed == 2.0  # Clamped to max
    
    @pytest.mark.asyncio
    async def test_set_replay_angle(self, replay_controller):
        """Test setting replay angle"""
        clip = replay_controller.create_clip("Test Clip")
        await replay_controller.play_replay(clip.clip_id)
        
        result = await replay_controller.set_replay_angle("overhead")
        
        assert result is True
        assert clip.angle == "overhead"
    
    @pytest.mark.asyncio
    async def test_set_replay_angle_no_active(self, replay_controller):
        """Test setting angle when no replay active"""
        result = await replay_controller.set_replay_angle("player_pov")
        assert result is False
    
    def test_get_stats(self, replay_controller):
        """Test getting replay controller statistics"""
        stats = replay_controller.get_stats()
        
        assert stats["state"] == "stopped"
        assert stats["buffer_active"] is False
        assert stats["total_clips"] == 0
        assert stats["queue_size"] == 0
        assert stats["total_played"] == 0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AUDIO MIXER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestAudioMixer:
    """Test Audio Mixer"""
    
    @pytest.fixture
    def mock_obs(self):
        """Create mock OBS client"""
        obs = AsyncMock()
        obs.set_volume = AsyncMock(return_value=True)
        obs.set_mute = AsyncMock(return_value=True)
        obs.send_command = AsyncMock(return_value=OBSResponse(success=True))
        return obs
    
    @pytest.fixture
    def audio_mixer(self, mock_obs):
        """Create audio mixer"""
        return AudioMixer(mock_obs)
    
    @pytest.mark.asyncio
    async def test_add_source(self, audio_mixer):
        """Test adding audio source"""
        result = await audio_mixer.add_source(
            name="Game Audio",
            channel=AudioChannel.GAME,
            volume=0.8,
        )
        
        assert result is True
        assert "Game Audio" in audio_mixer.sources
        assert audio_mixer.sources["Game Audio"].channel == AudioChannel.GAME
        assert audio_mixer.sources["Game Audio"].volume == 0.8
    
    @pytest.mark.asyncio
    async def test_remove_source(self, audio_mixer):
        """Test removing audio source"""
        await audio_mixer.add_source("Test Source", AudioChannel.GAME)
        
        result = await audio_mixer.remove_source("Test Source")
        assert result is True
        assert "Test Source" not in audio_mixer.sources
    
    @pytest.mark.asyncio
    async def test_set_source_volume(self, audio_mixer):
        """Test setting source volume"""
        await audio_mixer.add_source("Game", AudioChannel.GAME, 0.8)
        
        result = await audio_mixer.set_source_volume("Game", 0.5)
        
        assert result is True
        assert audio_mixer.sources["Game"].volume == 0.5
    
    @pytest.mark.asyncio
    async def test_set_source_mute(self, audio_mixer):
        """Test muting source"""
        await audio_mixer.add_source("Mic", AudioChannel.MIC)
        
        result = await audio_mixer.set_source_mute("Mic", True)
        assert result is True
        
        result = await audio_mixer.set_source_mute("Mic", False)
        assert result is True
    
    @pytest.mark.asyncio
    async def test_set_channel_volume(self, audio_mixer):
        """Test setting channel volume"""
        await audio_mixer.add_source("Game1", AudioChannel.GAME, 0.8)
        await audio_mixer.add_source("Game2", AudioChannel.GAME, 0.7)
        await audio_mixer.add_source("Commentary", AudioChannel.COMMENTARY, 1.0)
        
        await audio_mixer.set_channel_volume(AudioChannel.GAME, 0.5)
        
        assert audio_mixer.channel_volumes[AudioChannel.GAME] == 0.5
        # Commentary channel should be unchanged
        assert audio_mixer.channel_volumes[AudioChannel.COMMENTARY] == 1.0
    
    @pytest.mark.asyncio
    async def test_mute_unmute_channel(self, audio_mixer):
        """Test muting entire channel"""
        await audio_mixer.add_source("Game1", AudioChannel.GAME)
        await audio_mixer.add_source("Game2", AudioChannel.GAME)
        
        await audio_mixer.mute_channel(AudioChannel.GAME)
        await audio_mixer.unmute_channel(AudioChannel.GAME)
        
        assert True  # No exceptions
    
    @pytest.mark.asyncio
    async def test_duck_for_commentary(self, audio_mixer):
        """Test audio ducking for commentary"""
        await audio_mixer.add_source("Game Audio", AudioChannel.GAME, 0.8)
        await audio_mixer.add_source("Background Music", AudioChannel.MUSIC, 0.5)
        await audio_mixer.add_source("Commentary", AudioChannel.COMMENTARY, 1.0)
        
        # Enable ducking
        await audio_mixer.duck_for_commentary(enable=True)
        assert audio_mixer.is_ducking is True
        
        # Disable ducking
        await audio_mixer.duck_for_commentary(enable=False)
        assert audio_mixer.is_ducking is False
    
    @pytest.mark.asyncio
    async def test_duck_for_replay(self, audio_mixer):
        """Test audio ducking during replay"""
        await audio_mixer.add_source("Game Audio", AudioChannel.GAME, 0.8)
        await audio_mixer.add_source("Commentary", AudioChannel.COMMENTARY, 1.0)
        await audio_mixer.add_source("Replay Audio", AudioChannel.REPLAY, 1.0)
        
        await audio_mixer.duck_for_replay()
        
        assert True  # No exceptions
    
    def test_save_load_preset(self, audio_mixer):
        """Test saving and loading audio presets"""
        audio_mixer.sources["Game"] = MagicMock(volume=0.8)
        audio_mixer.sources["Commentary"] = MagicMock(volume=1.0)
        audio_mixer.sources["Music"] = MagicMock(volume=0.4)
        
        audio_mixer.save_preset("broadcast_default")
        
        assert "broadcast_default" in audio_mixer.presets
        assert len(audio_mixer.presets["broadcast_default"]) == 3
    
    @pytest.mark.asyncio
    async def test_load_preset(self, audio_mixer):
        """Test loading audio preset"""
        audio_mixer.presets["test_preset"] = {
            "Game": 0.6,
            "Commentary": 0.9,
        }
        
        await audio_mixer.add_source("Game", AudioChannel.GAME, 1.0)
        await audio_mixer.add_source("Commentary", AudioChannel.COMMENTARY, 1.0)
        
        result = await audio_mixer.load_preset("test_preset")
        assert result is True
    
    @pytest.mark.asyncio
    async def test_load_nonexistent_preset(self, audio_mixer):
        """Test loading non-existent preset"""
        result = await audio_mixer.load_preset("nonexistent")
        assert result is False
    
    def test_get_stats(self, audio_mixer):
        """Test getting audio mixer statistics"""
        audio_mixer.sources["Game"] = MagicMock(
            channel=AudioChannel.GAME, volume=0.8, muted=False
        )
        
        stats = audio_mixer.get_stats()
        
        assert stats["sources"] == 1
        assert "channels" in stats
        assert "ducking" in stats
        assert "presets" in stats


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RECORDING MANAGER TESTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestRecordingManager:
    """Test Recording Manager"""
    
    @pytest.fixture
    def mock_obs(self):
        """Create mock OBS client"""
        obs = AsyncMock()
        obs.start_recording = AsyncMock(return_value=True)
        obs.stop_recording = AsyncMock(return_value=True)
        obs.send_command = AsyncMock(return_value=OBSResponse(success=True, data={}))
        return obs
    
    @pytest.fixture
    def recording_manager(self, mock_obs):
        """Create recording manager"""
        return RecordingManager(mock_obs)
    
    @pytest.mark.asyncio
    async def test_start_recording(self, recording_manager):
        """Test starting a recording"""
        recording_id = await recording_manager.start_recording(
            match_id="match_001",
            tournament_name="AEGIS Invitational",
            quality="high",
        )
        
        assert recording_id is not None
        assert recording_manager.is_recording() is True
        assert recording_manager.active_recording is not None
        assert recording_manager.active_recording.match_id == "match_001"
        assert recording_manager.active_recording.status == RecordingStatus.RECORDING
    
    @pytest.mark.asyncio
    async def test_start_recording_while_recording(self, recording_manager):
        """Test starting recording when already recording"""
        await recording_manager.start_recording(match_id="match_001")
        
        # Try to start another
        result = await recording_manager.start_recording(match_id="match_002")
        
        assert result is None  # Should not start second recording
    
    @pytest.mark.asyncio
    async def test_stop_recording(self, recording_manager):
        """Test stopping a recording"""
        await recording_manager.start_recording(match_id="match_001")
        
        result = await recording_manager.stop_recording()
        
        assert result is True
        assert recording_manager.is_recording() is False
        assert recording_manager.active_recording is None
    
    @pytest.mark.asyncio
    async def test_stop_recording_not_recording(self, recording_manager):
        """Test stopping when not recording"""
        result = await recording_manager.stop_recording()
        assert result is False
    
    @pytest.mark.asyncio
    async def test_pause_recording(self, recording_manager):
        """Test pausing recording"""
        await recording_manager.start_recording(match_id="match_001")
        
        result = await recording_manager.pause_recording()
        
        assert result is True
        assert recording_manager.active_recording.status == RecordingStatus.PAUSED
    
    @pytest.mark.asyncio
    async def test_resume_recording(self, recording_manager):
        """Test resuming recording"""
        await recording_manager.start_recording(match_id="match_001")
        await recording_manager.pause_recording()
        
        result = await recording_manager.resume_recording()
        
        assert result is True
        assert recording_manager.active_recording.status == RecordingStatus.RECORDING
    
    @pytest.mark.asyncio
    async def test_set_quality(self, recording_manager):
        """Test setting recording quality"""
        result = await recording_manager.set_quality("high")
        assert result is True
        
        result = await recording_manager.set_quality("medium")
        assert result is True
        
        result = await recording_manager.set_quality("low")
        assert result is True
    
    def test_get_recording(self, recording_manager):
        """Test getting recording by ID"""
        recording_manager.recordings["rec_001"] = MagicMock(
            recording_id="rec_001",
            filename="test_recording",
            match_id="match_001",
        )
        
        rec = recording_manager.get_recording("rec_001")
        assert rec is not None
        assert rec.filename == "test_recording"
        
        rec = recording_manager.get_recording("nonexistent")
        assert rec is None
    
    def test_get_active_recording(self, recording_manager):
        """Test getting active recording"""
        assert recording_manager.get_active_recording() is None
        
        recording_manager.active_recording = MagicMock(
            recording_id="active_rec",
            filename="active_recording",
        )
        
        active = recording_manager.get_active_recording()
        assert active is not None
        assert active.recording_id == "active_rec"
    
    def test_is_recording(self, recording_manager):
        """Test checking if recording"""
        assert recording_manager.is_recording() is False
        
        recording_manager.active_recording = MagicMock(
            status=RecordingStatus.RECORDING
        )
        assert recording_manager.is_recording() is True
        
        recording_manager.active_recording.status = RecordingStatus.PAUSED
        assert recording_manager.is_recording() is False
    
    def test_get_stats(self, recording_manager):
        """Test getting recording statistics"""
        recording_manager._total_recordings = 10
        recording_manager._total_duration = 7200  # 2 hours
        
        stats = recording_manager.get_stats()
        
        assert stats["active_recording"] is None
        assert stats["is_recording"] is False
        assert stats["total_recordings"] == 10
        assert stats["total_duration_hours"] == 2.0
        assert "recent_recordings" in stats
        assert "output_dir" in stats


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# INTEGRATED BROADCAST WORKFLOW TEST
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@pytest.mark.integration
@pytest.mark.broadcast
class TestBroadcastWorkflow:
    """Test complete broadcast workflow integration"""
    
    @pytest.fixture
    def mock_obs(self):
        """Create comprehensive mock OBS client"""
        obs = AsyncMock()
        obs.send_command = AsyncMock(return_value=OBSResponse(success=True, data={}))
        obs.switch_scene = AsyncMock(return_value=True)
        obs.start_recording = AsyncMock(return_value=True)
        obs.stop_recording = AsyncMock(return_value=True)
        obs.set_volume = AsyncMock(return_value=True)
        obs.set_mute = AsyncMock(return_value=True)
        obs.set_source_visibility = AsyncMock(return_value=True)
        obs.scene_list = [
            "Main Camera", "Team A POV", "Team B POV",
            "Map Overview", "Fight Coverage", "Clutch Focus",
            "Replay", "Scoreboard", "Intermission",
            "Starting Soon", "Match End", "Cinematic"
        ]
        return obs
    
    @pytest.mark.asyncio
    async def test_full_broadcast_setup(self, mock_obs):
        """Test setting up complete broadcast environment"""
        # Initialize all managers
        scene_manager = SceneManager(mock_obs)
        source_manager = SourceManager(mock_obs)
        transition_controller = TransitionController(mock_obs)
        audio_mixer = AudioMixer(mock_obs)
        replay_controller = ReplayController(mock_obs)
        recording_manager = RecordingManager(mock_obs)
        
        # Setup scenes
        await scene_manager.setup_broadcast_scenes()
        assert len(scene_manager.scenes) >= 10
        
        # Add audio sources
        await audio_mixer.add_source("Game Audio", AudioChannel.GAME, 0.8)
        await audio_mixer.add_source("Commentary", AudioChannel.COMMENTARY, 1.0)
        await audio_mixer.add_source("Background Music", AudioChannel.MUSIC, 0.3)
        assert len(audio_mixer.sources) == 3
        
        # Start replay buffer
        await replay_controller.start_buffer()
        assert replay_controller.state == ReplayState.RECORDING
        
        # Start recording
        rec_id = await recording_manager.start_recording(
            match_id="match_001",
            tournament_name="AEGIS Invitational",
        )
        assert rec_id is not None
        assert recording_manager.is_recording() is True
        
        # Simulate broadcast flow
        # Switch to starting soon
        await scene_manager.switch_to("Starting Soon")
        assert scene_manager.active_scene == "Starting Soon"
        
        # Duck for commentary
        await audio_mixer.duck_for_commentary(enable=True)
        assert audio_mixer.is_ducking is True
        
        # Switch to main camera
        await scene_manager.switch_to("Main Camera")
        assert scene_manager.active_scene == "Main Camera"
        
        # Create and play replay
        clip = replay_controller.create_clip("Test Highlight", excitement=0.8)
        await replay_controller.play_replay(clip.clip_id)
        assert replay_controller.state == ReplayState.PLAYING
        
        await replay_controller.stop_replay()
        assert replay_controller.state == ReplayState.STOPPED
        
        # Switch to match end
        await scene_manager.switch_to("Match End")
        assert scene_manager.active_scene == "Match End"
        
        # Stop recording
        await recording_manager.stop_recording()
        assert recording_manager.is_recording() is False
        
        # Stop replay buffer
        await replay_controller.stop_buffer()
        assert replay_controller.state == ReplayState.STOPPED
        
        # Verify all managers have correct stats
        scene_stats = scene_manager.get_stats()
        assert scene_stats["scene_count"] >= 10
        
        audio_stats = audio_mixer.get_stats()
        assert audio_stats["sources"] == 3
        
        replay_stats = replay_controller.get_stats()
        assert replay_stats["total_clips"] == 1
        assert replay_stats["total_played"] == 1
        
        rec_stats = recording_manager.get_stats()
        assert rec_stats["total_recordings"] == 1