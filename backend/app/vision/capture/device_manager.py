# backend/app/vision/capture/device_manager.py
"""
AEGIS AI - Device Manager
Manages 16 spectator devices (Android phones/tablets).
Handles connection, monitoring, and automated control.
"""

import asyncio
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.vision.devices")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DeviceStatus(str, Enum):
    """Device connection status"""
    ONLINE = "online"
    OFFLINE = "offline"
    CONNECTING = "connecting"
    RECONNECTING = "reconnecting"
    ERROR = "error"
    MAINTENANCE = "maintenance"
    BUSY = "busy"
    IDLE = "idle"

class DeviceType(str, Enum):
    """Types of spectator devices"""
    ANDROID_PHONE = "android_phone"
    ANDROID_TABLET = "android_tablet"
    EMULATOR = "emulator"
    IPHONE = "iphone"
    IPAD = "ipad"

class ConnectionType(str, Enum):
    """Device connection methods"""
    USB = "usb"
    WIFI = "wifi"
    TCPIP = "tcpip"
    EMULATOR_BRIDGE = "emulator_bridge"

@dataclass
class SpectatorDevice:
    """A single spectator device"""
    device_id: str
    device_name: str
    device_type: DeviceType = DeviceType.ANDROID_PHONE
    connection_type: ConnectionType = ConnectionType.USB
    
    # Connection
    serial_number: str = ""
    ip_address: str = ""
    adb_port: int = 5555
    usb_port: str = ""
    
    # Status
    status: DeviceStatus = DeviceStatus.OFFLINE
    last_seen: float = 0.0
    connection_attempts: int = 0
    max_reconnect_attempts: int = 5
    
    # Assignment
    assigned_team_id: str = ""
    assigned_team_name: str = ""
    assigned_player_id: str = ""
    spectator_slot: int = 0  # 1-16
    
    # Hardware
    screen_width: int = 1920
    screen_height: int = 1080
    screen_density: int = 480
    android_version: str = ""
    bgmi_version: str = ""
    
    # Performance
    current_fps: float = 0.0
    avg_fps: float = 0.0
    battery_level: int = 100
    battery_temp: float = 25.0
    cpu_usage: float = 0.0
    memory_usage: float = 0.0
    
    # State
    is_bgmi_running: bool = False
    is_in_match: bool = False
    current_activity: str = ""
    last_frame_time: float = 0.0
    frame_drops: int = 0
    
    # USB hub
    usb_hub_port: str = ""
    usb_bandwidth_used: float = 0.0
    
    # Health
    health_score: float = 100.0
    needs_restart: bool = False
    restart_count: int = 0
    
    def get_resolution_string(self) -> str:
        """Get resolution as string"""
        return f"{self.screen_width}x{self.screen_height}"
    
    def is_healthy(self) -> bool:
        """Check if device is healthy"""
        return (
            self.status == DeviceStatus.ONLINE
            and self.battery_level > 15
            and self.health_score > 50
            and not self.needs_restart
        )


class DeviceManager:
    """
    Manages all spectator devices for a tournament.
    
    Features:
    - Auto-discovery of connected devices
    - USB hub management
    - Device health monitoring
    - Automatic recovery (restart BGMI, reconnect)
    - Load balancing across USB hubs
    - Team assignment management
    - Battery and temperature monitoring
    """
    
    def __init__(self):
        # All devices
        self.devices: Dict[str, SpectatorDevice] = {}
        
        # USB hub mapping
        self.usb_hubs: Dict[str, List[str]] = {}  # hub_id -> [device_ids]
        
        # Device history
        self.device_history: deque = deque(maxlen=1000)
        
        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Configuration
        self.max_devices = 16
        self.max_devices_per_hub = 4
        self.health_check_interval = 5  # seconds
        self.reconnect_delay = 3  # seconds
        
        self._log = logger.bind(component="device_manager")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def register_device(
        self,
        device_id: str,
        device_name: str,
        device_type: DeviceType = DeviceType.ANDROID_PHONE,
        serial_number: str = "",
        connection_type: ConnectionType = ConnectionType.USB,
    ) -> SpectatorDevice:
        """Register a new device"""
        
        device = SpectatorDevice(
            device_id=device_id,
            device_name=device_name,
            device_type=device_type,
            serial_number=serial_number,
            connection_type=connection_type,
        )
        
        self.devices[device_id] = device
        
        self._log.info(
            f"Device registered: {device_name}",
            device_id=device_id,
            type=device_type.value,
        )
        
        return device
    
    def unregister_device(self, device_id: str) -> bool:
        """Remove a device"""
        if device_id in self.devices:
            device = self.devices.pop(device_id)
            
            # Remove from USB hub
            for hub_id, device_ids in self.usb_hubs.items():
                if device_id in device_ids:
                    device_ids.remove(device_id)
            
            self._log.info(f"Device unregistered: {device.device_name}")
            return True
        
        return False
    
    def assign_team(
        self,
        device_id: str,
        team_id: str,
        team_name: str,
        spectator_slot: int,
    ) -> bool:
        """Assign a device to spectate a specific team"""
        
        device = self.devices.get(device_id)
        if not device:
            return False
        
        device.assigned_team_id = team_id
        device.assigned_team_name = team_name
        device.spectator_slot = spectator_slot
        
        self._log.info(
            f"Device assigned: {device.device_name} → {team_name}",
            slot=spectator_slot,
        )
        
        return True
    
    def assign_to_usb_hub(self, device_id: str, hub_id: str, port: str) -> bool:
        """Assign device to a USB hub port"""
        
        device = self.devices.get(device_id)
        if not device:
            return False
        
        # Check hub capacity
        if hub_id in self.usb_hubs:
            if len(self.usb_hubs[hub_id]) >= self.max_devices_per_hub:
                self._log.warning(f"USB hub {hub_id} is full")
                return False
        
        # Add to hub
        if hub_id not in self.usb_hubs:
            self.usb_hubs[hub_id] = []
        
        self.usb_hubs[hub_id].append(device_id)
        device.usb_hub_port = f"{hub_id}:{port}"
        
        self._log.info(
            f"Device {device.device_name} assigned to USB hub {hub_id}:{port}"
        )
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MONITORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start_monitoring(self) -> None:
        """Start device health monitoring"""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self._log.info("Device monitoring started")
    
    async def stop_monitoring(self) -> None:
        """Stop device health monitoring"""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        self._log.info("Device monitoring stopped")
    
    async def _monitor_loop(self) -> None:
        """Continuous device monitoring loop"""
        while self._running:
            try:
                for device in self.devices.values():
                    await self._check_device_health(device)
                
                await asyncio.sleep(self.health_check_interval)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Monitor error: {e}")
    
    async def _check_device_health(self, device: SpectatorDevice) -> None:
        """Check health of a single device"""
        
        # Update health score
        health = 100.0
        
        # Battery check
        if device.battery_level < 10:
            health -= 40
        elif device.battery_level < 20:
            health -= 20
        elif device.battery_level < 30:
            health -= 10
        
        # Temperature check
        if device.battery_temp > 45:
            health -= 30
        elif device.battery_temp > 40:
            health -= 15
        
        # FPS check
        if device.current_fps < 10:
            health -= 25
        elif device.current_fps < 20:
            health -= 10
        
        # Frame drops
        if device.frame_drops > 50:
            health -= 20
        
        # BGMI check
        if not device.is_bgmi_running:
            health -= 30
            device.needs_restart = True
        
        device.health_score = max(0, health)
        
        # Check if device needs attention
        if device.health_score < 40:
            self._log.warning(
                f"Device unhealthy: {device.device_name}",
                health=round(health, 1),
                battery=device.battery_level,
                temp=device.battery_temp,
                fps=device.current_fps,
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEVICE CONTROL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_device_for_team(self, team_id: str) -> Optional[SpectatorDevice]:
        """Get the device assigned to a team"""
        for device in self.devices.values():
            if device.assigned_team_id == team_id:
                return device
        return None
    
    def get_idle_devices(self) -> List[SpectatorDevice]:
        """Get devices not assigned to any team"""
        return [
            d for d in self.devices.values()
            if not d.assigned_team_id
        ]
    
    def get_healthy_devices(self) -> List[SpectatorDevice]:
        """Get all healthy devices"""
        return [d for d in self.devices.values() if d.is_healthy()]
    
    def get_unhealthy_devices(self) -> List[SpectatorDevice]:
        """Get devices needing attention"""
        return [d for d in self.devices.values() if not d.is_healthy()]
    
    def get_device_summary(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed device summary"""
        device = self.devices.get(device_id)
        if not device:
            return None
        
        return {
            "device_id": device.device_id,
            "name": device.device_name,
            "status": device.status.value,
            "team": device.assigned_team_name,
            "health": round(device.health_score, 1),
            "battery": device.battery_level,
            "temperature": round(device.battery_temp, 1),
            "fps": round(device.current_fps, 1),
            "resolution": device.get_resolution_string(),
            "bgmi_running": device.is_bgmi_running,
            "needs_restart": device.needs_restart,
            "usb_hub": device.usb_hub_port,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get device manager statistics"""
        devices = list(self.devices.values())
        
        return {
            "total_devices": len(devices),
            "online": len([d for d in devices if d.status == DeviceStatus.ONLINE]),
            "offline": len([d for d in devices if d.status == DeviceStatus.OFFLINE]),
            "healthy": len(self.get_healthy_devices()),
            "unhealthy": len(self.get_unhealthy_devices()),
            "assigned": len([d for d in devices if d.assigned_team_id]),
            "idle": len(self.get_idle_devices()),
            "usb_hubs": {
                hub_id: len(device_ids)
                for hub_id, device_ids in self.usb_hubs.items()
            },
            "devices": [
                self.get_device_summary(d.device_id)
                for d in devices
            ],
        }
    
    def reset(self) -> None:
        """Reset device manager"""
        self.devices.clear()
        self.usb_hubs.clear()
        self._log.info("Device manager reset")