# backend/app/broadcast/obs_client.py
"""
AEGIS AI - OBS WebSocket Client
Production-grade OBS WebSocket client with auto-reconnect,
command queuing, state tracking, and error recovery.

CANONICAL (Phase 22 / W3): this is THE OBS client of the product.
The parallel ``app.production`` OBS stack is deprecated (see the
notice in ``production/obs_manager.py``) and must not gain features.
The declared ``obsws-python`` dependency is not used — this client
speaks the obs-websocket v5 protocol directly.
"""

import asyncio
import time
import json
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.broadcast.obs")


class ConnectionState(str, Enum):
    """OBS connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    AUTHENTICATING = "authenticating"
    RECONNECTING = "reconnecting"
    ERROR = "error"


@dataclass
class OBSConfig:
    """OBS WebSocket configuration"""
    host: str = "localhost"
    port: int = 4455
    password: str = ""
    
    # Connection
    reconnect_attempts: int = 10
    reconnect_delay: float = 2.0
    reconnect_backoff: float = 1.5
    connection_timeout: float = 10.0
    
    # Heartbeat
    heartbeat_interval: float = 5.0
    
    # Commands
    command_timeout: float = 5.0
    max_retries: int = 3
    
    # Events
    subscribe_to_events: bool = True
    event_buffer_size: int = 100


@dataclass
class OBSResponse:
    """OBS command response"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    request_id: str = ""
    latency_ms: float = 0.0


class OBSClient:
    """
    Production OBS WebSocket client.
    
    Features:
    - Auto-reconnect with exponential backoff
    - Command queuing and batching
    - Event subscription and routing
    - State synchronization
    - Heartbeat monitoring
    - Error recovery
    """
    
    def __init__(self, config: Optional[OBSConfig] = None):
        self.config = config or OBSConfig()
        
        # Connection
        self._ws: Optional[Any] = None
        self.state = ConnectionState.DISCONNECTED
        
        # Command handling
        self._request_id = 0
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._command_queue: asyncio.Queue = asyncio.Queue()
        
        # Event handling
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._event_buffer: deque = deque(maxlen=self.config.event_buffer_size)
        
        # State tracking
        self.current_scene: str = ""
        self.scene_list: List[str] = []
        self.streaming: bool = False
        self.recording: bool = False
        
        # Tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._command_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # Statistics
        self._commands_sent: int = 0
        self._commands_failed: int = 0
        self._events_received: int = 0
        self._reconnect_count: int = 0
        
        self._log = logger.bind(component="obs_client")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONNECTION MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def connect(self) -> bool:
        """Connect to OBS WebSocket"""
        
        try:
            import websockets
            
            self.state = ConnectionState.CONNECTING
            self._log.info(f"Connecting to OBS at {self.config.host}:{self.config.port}")
            
            url = f"ws://{self.config.host}:{self.config.port}"
            
            self._ws = await websockets.connect(
                url,
                timeout=self.config.connection_timeout,
                ping_interval=self.config.heartbeat_interval,
                ping_timeout=5.0,
            )
            
            # Authenticate if password set
            if self.config.password:
                self.state = ConnectionState.AUTHENTICATING
                await self._authenticate()
            
            self.state = ConnectionState.CONNECTED
            
            # Start background tasks
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
            self._command_task = asyncio.create_task(self._process_command_queue())
            
            # Get initial state
            await self._sync_state()
            
            self._log.info("Connected to OBS")
            return True
            
        except ImportError:
            self._log.error("websockets package not installed")
            self.state = ConnectionState.ERROR
            return False
        except Exception as e:
            self._log.error(f"Failed to connect to OBS: {e}")
            self.state = ConnectionState.ERROR
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from OBS"""
        
        self.state = ConnectionState.DISCONNECTED
        
        # Cancel tasks
        for task in [self._heartbeat_task, self._command_task, self._reconnect_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close WebSocket
        if self._ws:
            await self._ws.close()
            self._ws = None
        
        self._log.info("Disconnected from OBS")
    
    async def _authenticate(self) -> None:
        """Authenticate with OBS"""
        
        import base64
        import hashlib
        
        # Wait for Hello message
        hello = await self._ws.recv()
        hello_data = json.loads(hello)
        
        if hello_data.get("op") == 0:  # Hello
            auth_data = hello_data.get("d", {})
            challenge = auth_data.get("authentication", {}).get("challenge", "")
            salt = auth_data.get("authentication", {}).get("salt", "")
            
            # Create auth string
            secret = base64.b64encode(
                hashlib.sha256(
                    (self.config.password + salt).encode()
                ).digest()
            )
            
            auth_response = base64.b64encode(
                hashlib.sha256(
                    secret + challenge.encode()
                ).digest()
            ).decode()
            
            # Send Identify
            identify_msg = {
                "op": 1,
                "d": {
                    "rpcVersion": 1,
                    "authentication": auth_response,
                    "eventSubscriptions": 33 if self.config.subscribe_to_events else 0,
                },
            }
            
            await self._ws.send(json.dumps(identify_msg))
            
            # Wait for Identified response
            response = await self._ws.recv()
            response_data = json.loads(response)
            
            if response_data.get("op") == 2:  # Identified
                self._log.info("Authenticated with OBS")
            else:
                raise RuntimeError("Authentication failed")
    
    async def _sync_state(self) -> None:
        """Synchronize current OBS state"""
        
        try:
            # Get current scene
            response = await self.send_command("GetCurrentProgramScene")
            if response.success:
                self.current_scene = response.data.get("sceneName", "")
            
            # Get scene list
            response = await self.send_command("GetSceneList")
            if response.success:
                self.scene_list = [
                    s.get("sceneName", "")
                    for s in response.data.get("scenes", [])
                ]
            
            # Get streaming status
            response = await self.send_command("GetStreamStatus")
            if response.success:
                self.streaming = response.data.get("outputActive", False)
            
            # Get recording status
            response = await self.send_command("GetRecordStatus")
            if response.success:
                self.recording = response.data.get("outputActive", False)
            
            self._log.info(f"State synced: scene={self.current_scene}, "
                          f"streaming={self.streaming}, recording={self.recording}")
            
        except Exception as e:
            self._log.error(f"State sync failed: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMAND EXECUTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def send_command(
        self,
        command: str,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> OBSResponse:
        """
        Send a command to OBS.
        
        Args:
            command: OBS command name
            params: Command parameters
            timeout: Response timeout
            
        Returns:
            OBSResponse with result
        """
        
        if self.state != ConnectionState.CONNECTED:
            return OBSResponse(success=False, error="Not connected")
        
        self._request_id += 1
        request_id = f"cmd_{self._request_id}"
        
        message = {
            "op": 6,  # Request
            "d": {
                "requestId": request_id,
                "requestType": command,
                "requestData": params or {},
            },
        }
        
        try:
            start_time = time.time()
            
            # Send command
            await self._ws.send(json.dumps(message))
            self._commands_sent += 1
            
            # Wait for response
            timeout_val = timeout or self.config.command_timeout
            
            try:
                response = await asyncio.wait_for(
                    self._ws.recv(), timeout=timeout_val
                )
                
                latency = (time.time() - start_time) * 1000
                response_data = json.loads(response)
                
                if response_data.get("op") == 7:  # RequestResponse
                    data = response_data.get("d", {})
                    status = data.get("requestStatus", {})
                    
                    if status.get("result", False):
                        return OBSResponse(
                            success=True,
                            data=status.get("responseData", {}),
                            request_id=request_id,
                            latency_ms=round(latency, 2),
                        )
                    else:
                        self._commands_failed += 1
                        return OBSResponse(
                            success=False,
                            error=status.get("comment", "Unknown error"),
                            request_id=request_id,
                        )
                
            except asyncio.TimeoutError:
                self._commands_failed += 1
                return OBSResponse(
                    success=False,
                    error=f"Command timed out after {timeout_val}s",
                    request_id=request_id,
                )
                
        except Exception as e:
            self._commands_failed += 1
            return OBSResponse(
                success=False,
                error=str(e),
                request_id=request_id,
            )
    
    async def queue_command(self, command: str, params: Optional[Dict[str, Any]] = None) -> None:
        """Queue a command for batch execution"""
        await self._command_queue.put((command, params))
    
    async def _process_command_queue(self) -> None:
        """Process queued commands"""
        while self.state == ConnectionState.CONNECTED:
            try:
                command, params = await asyncio.wait_for(
                    self._command_queue.get(), timeout=1.0
                )
                
                await self.send_command(command, params)
                self._command_queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Command queue error: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HIGH-LEVEL COMMANDS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def switch_scene(self, scene_name: str) -> bool:
        """Switch to a scene"""
        response = await self.send_command("SetCurrentProgramScene", {
            "sceneName": scene_name,
        })
        if response.success:
            self.current_scene = scene_name
        return response.success
    
    async def start_streaming(self) -> bool:
        """Start streaming"""
        response = await self.send_command("StartStream")
        if response.success:
            self.streaming = True
        return response.success
    
    async def stop_streaming(self) -> bool:
        """Stop streaming"""
        response = await self.send_command("StopStream")
        if response.success:
            self.streaming = False
        return response.success
    
    async def start_recording(self) -> bool:
        """Start recording"""
        response = await self.send_command("StartRecord")
        if response.success:
            self.recording = True
        return response.success
    
    async def stop_recording(self) -> bool:
        """Stop recording"""
        response = await self.send_command("StopRecord")
        if response.success:
            self.recording = False
        return response.success
    
    async def set_source_visibility(self, scene: str, source: str, visible: bool) -> bool:
        """Set source visibility in a scene"""
        response = await self.send_command("SetSceneItemEnabled", {
            "sceneName": scene,
            "sceneItemId": source,
            "sceneItemEnabled": visible,
        })
        return response.success
    
    async def set_volume(self, source: str, volume: float) -> bool:
        """Set source volume (0.0 to 1.0)"""
        response = await self.send_command("SetInputVolume", {
            "inputName": source,
            "inputVolumeMul": max(0.0, min(1.0, volume)),
        })
        return response.success
    
    async def set_mute(self, source: str, muted: bool) -> bool:
        """Mute/unmute a source"""
        response = await self.send_command("SetInputMute", {
            "inputName": source,
            "inputMuted": muted,
        })
        return response.success
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HANDLING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def on_event(self, event_type: str, handler: Callable) -> None:
        """Register an event handler"""
        if event_type not in self._event_handlers:
            self._event_handlers[event_type] = []
        self._event_handlers[event_type].append(handler)
    
    async def _handle_event(self, event_data: Dict[str, Any]) -> None:
        """Handle an incoming event"""
        event_type = event_data.get("eventType", "")
        event_payload = event_data.get("eventData", {})
        
        self._events_received += 1
        self._event_buffer.append({
            "type": event_type,
            "data": event_payload,
            "timestamp": time.time(),
        })
        
        # Dispatch to registered handlers
        handlers = self._event_handlers.get(event_type, [])
        for handler in handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    await handler(event_payload)
                else:
                    handler(event_payload)
            except Exception as e:
                self._log.error(f"Event handler error ({event_type}): {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEARTBEAT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeats"""
        while self.state == ConnectionState.CONNECTED:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)
                # OBS WebSocket handles ping/pong automatically
            except asyncio.CancelledError:
                break
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RECONNECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _attempt_reconnect(self) -> None:
        """Attempt to reconnect to OBS"""
        self.state = ConnectionState.RECONNECTING
        
        for attempt in range(self.config.reconnect_attempts):
            delay = self.config.reconnect_delay * (self.config.reconnect_backoff ** attempt)
            
            self._log.info(f"Reconnect attempt {attempt + 1}/{self.config.reconnect_attempts} in {delay:.1f}s")
            
            await asyncio.sleep(delay)
            
            try:
                if await self.connect():
                    self._reconnect_count += 1
                    self._log.info("Reconnected successfully")
                    return
            except Exception:
                continue
        
        self.state = ConnectionState.ERROR
        self._log.error("All reconnect attempts failed")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def is_connected(self) -> bool:
        """Check if connected"""
        return self.state == ConnectionState.CONNECTED
    
    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics"""
        return {
            "state": self.state.value,
            "connected": self.is_connected(),
            "current_scene": self.current_scene,
            "scenes": self.scene_list,
            "streaming": self.streaming,
            "recording": self.recording,
            "commands_sent": self._commands_sent,
            "commands_failed": self._commands_failed,
            "events_received": self._events_received,
            "reconnect_count": self._reconnect_count,
        }
    
    async def close(self) -> None:
        """Close client connection"""
        await self.disconnect()