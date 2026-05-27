# backend/app/overlays/overlay_server.py
"""
AEGIS AI - Overlay WebSocket Server
Real-time WebSocket server for browser-based broadcast overlays.
Streams match data, events, and statistics to OBS browser sources.
"""

import time
import json
from typing import Dict, List, Optional, Any, Set, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
import structlog

logger = structlog.get_logger("aegis.overlays.server")


class OverlayType(str, Enum):
    """Types of broadcast overlays"""
    SCOREBOARD = "scoreboard"
    KILL_FEED = "kill_feed"
    MINIMAP = "minimap"
    TEAM_PANEL = "team_panel"
    PLAYER_INFO = "player_info"
    ALIVE_COUNTER = "alive_counter"
    ZONE_INDICATOR = "zone_indicator"
    COMMENTARY_BAR = "commentary_bar"
    WEAPON_DISPLAY = "weapon_display"
    UTILITY_COUNT = "utility_count"
    SPONSOR_BANNER = "sponsor_banner"
    REPLAY_HEADER = "replay_header"
    MATCH_RESULT = "match_result"
    MVP_DISPLAY = "mvp_display"
    CUSTOM = "custom"


@dataclass
class OverlayClient:
    """A connected overlay client"""
    client_id: str
    overlay_type: OverlayType
    connected_at: float = field(default_factory=time.time)
    
    # Connection
    websocket: Optional[Any] = None
    
    # Subscriptions
    subscribed_channels: Set[str] = field(default_factory=set)
    
    # Settings
    width: int = 1920
    height: int = 1080
    fps: int = 30
    refresh_rate_ms: int = 100  # How often to push updates
    
    # Statistics
    messages_received: int = 0
    messages_sent: int = 0
    last_activity: float = field(default_factory=time.time)


@dataclass
class OverlayMessage:
    """A message to/from an overlay"""
    message_type: str
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    target: Optional[str] = None  # Target overlay type or client_id
    priority: int = 50  # Higher = more important


class OverlayServer:
    """
    Real-time WebSocket server for broadcast overlays.
    
    Features:
    - Multi-client WebSocket connections
    - Type-based overlay routing
    - Channel-based subscriptions
    - Automatic reconnection
    - Heartbeat monitoring
    - Message queuing and batching
    - Client statistics
    """
    
    def __init__(self, host: str = "0.0.0.0", port: int = 9001):
        self.host = host
        self.port = port
        
        # Connected clients
        self.clients: Dict[str, OverlayClient] = {}
        self.clients_by_type: Dict[OverlayType, List[str]] = defaultdict(list)
        
        # Message handlers
        self._message_handlers: Dict[str, Callable] = {}
        
        # Server
        self._server: Optional[Any] = None
        self._running = False
        
        # Statistics
        self._total_connections = 0
        self._total_messages = 0
        
        self._log = logger.bind(component="overlay_server")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SERVER LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start(self) -> None:
        """Start the overlay WebSocket server"""
        
        try:
            import websockets
            
            self._running = True
            
            self._server = await websockets.serve(
                self._handle_connection,
                self.host,
                self.port,
                ping_interval=30,
                ping_timeout=10,
                max_size=2**20,  # 1MB max message
            )
            
            self._log.info(f"Overlay server started on ws://{self.host}:{self.port}")
            
        except ImportError:
            self._log.error("websockets package not installed")
        except Exception as e:
            self._log.error(f"Failed to start overlay server: {e}")
    
    async def stop(self) -> None:
        """Stop the overlay server"""
        
        self._running = False
        
        # Close all client connections
        for client in self.clients.values():
            try:
                if client.websocket:
                    await client.websocket.close(1000, "Server shutting down")
            except Exception:
                pass
        
        self.clients.clear()
        self.clients_by_type.clear()
        
        # Close server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        self._log.info("Overlay server stopped")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONNECTION HANDLING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _handle_connection(self, websocket: Any) -> None:
        """Handle a new WebSocket connection"""
        
        client_id = f"overlay_{int(time.time()*1000)}_{self._total_connections}"
        
        # Default client
        client = OverlayClient(
            client_id=client_id,
            overlay_type=OverlayType.CUSTOM,
        )
        client.websocket = websocket
        
        self.clients[client_id] = client
        self._total_connections += 1
        
        self._log.info(f"Overlay connected: {client_id}")
        
        try:
            # Send welcome message
            await self._send_message(client, {
                "type": "connected",
                "client_id": client_id,
                "server_time": time.time(),
            })
            
            # Handle messages
            async for message in websocket:
                await self._handle_message(client, message)
                client.messages_received += 1
                client.last_activity = time.time()
                
        except Exception as e:
            self._log.error(f"Connection error ({client_id}): {e}")
        finally:
            await self._handle_disconnect(client)
    
    async def _handle_disconnect(self, client: OverlayClient) -> None:
        """Handle client disconnection"""
        
        self.clients.pop(client.client_id, None)
        
        # Remove from type index
        if client.overlay_type in self.clients_by_type:
            self.clients_by_type[client.overlay_type].remove(client.client_id)
        
        self._log.info(f"Overlay disconnected: {client.client_id}")
    
    async def _handle_message(self, client: OverlayClient, raw_message: str) -> None:
        """Handle a message from an overlay client"""
        
        try:
            message = json.loads(raw_message)
            msg_type = message.get("type", "")
            data = message.get("data", {})
            
            # Handle overlay type registration
            if msg_type == "register":
                overlay_type = data.get("overlay_type", "custom")
                try:
                    client.overlay_type = OverlayType(overlay_type)
                    self.clients_by_type[client.overlay_type].append(client.client_id)
                    
                    await self._send_message(client, {
                        "type": "registered",
                        "overlay_type": overlay_type,
                    })
                    
                    self._log.info(f"Overlay registered: {client.client_id} as {overlay_type}")
                    
                except ValueError:
                    await self._send_message(client, {
                        "type": "error",
                        "message": f"Invalid overlay type: {overlay_type}",
                    })
            
            # Handle channel subscriptions
            elif msg_type == "subscribe":
                channels = data.get("channels", [])
                client.subscribed_channels.update(channels)
                
                await self._send_message(client, {
                    "type": "subscribed",
                    "channels": list(client.subscribed_channels),
                })
            
            # Handle settings update
            elif msg_type == "settings":
                client.width = data.get("width", client.width)
                client.height = data.get("height", client.height)
                client.fps = data.get("fps", client.fps)
                client.refresh_rate_ms = data.get("refresh_rate_ms", client.refresh_rate_ms)
            
            # Handle heartbeat/pong
            elif msg_type == "pong":
                client.last_activity = time.time()
            
            # Dispatch to custom handlers
            elif msg_type in self._message_handlers:
                await self._message_handlers[msg_type](client, data)
            
        except json.JSONDecodeError:
            self._log.warning(f"Invalid JSON from {client.client_id}")
        except Exception as e:
            self._log.error(f"Message handling error: {e}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MESSAGING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _send_message(self, client: OverlayClient, data: Dict[str, Any]) -> None:
        """Send a message to a specific client"""
        try:
            if client.websocket:
                data["timestamp"] = time.time()
                await client.websocket.send(json.dumps(data))
                client.messages_sent += 1
                self._total_messages += 1
        except Exception as e:
            self._log.error(f"Failed to send to {client.client_id}: {e}")
    
    async def broadcast_to_type(
        self,
        overlay_type: OverlayType,
        data: Dict[str, Any],
        priority: int = 50,
    ) -> int:
        """Broadcast a message to all overlays of a specific type"""
        
        client_ids = self.clients_by_type.get(overlay_type, [])
        sent = 0
        
        for client_id in client_ids:
            client = self.clients.get(client_id)
            if client and client.websocket:
                await self._send_message(client, data)
                sent += 1
        
        return sent
    
    async def broadcast_to_all(
        self,
        data: Dict[str, Any],
        priority: int = 50,
    ) -> int:
        """Broadcast a message to all connected overlays"""
        
        sent = 0
        
        for client in self.clients.values():
            if client.websocket:
                await self._send_message(client, data)
                sent += 1
        
        return sent
    
    async def send_to_client(self, client_id: str, data: Dict[str, Any]) -> bool:
        """Send a message to a specific client"""
        
        client = self.clients.get(client_id)
        if client:
            await self._send_message(client, data)
            return True
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HANDLERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def on_message(self, message_type: str, handler: Callable) -> None:
        """Register a message handler"""
        self._message_handlers[message_type] = handler
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_connected_clients(self) -> Dict[str, int]:
        """Get count of connected clients by type"""
        return {
            t.value: len(clients)
            for t, clients in self.clients_by_type.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get overlay server statistics"""
        return {
            "running": self._running,
            "total_connections": self._total_connections,
            "active_clients": len(self.clients),
            "total_messages": self._total_messages,
            "clients_by_type": self.get_connected_clients(),
            "host": f"ws://{self.host}:{self.port}",
        }