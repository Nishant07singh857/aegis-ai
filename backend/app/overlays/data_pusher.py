# backend/app/overlays/data_pusher.py
"""
AEGIS AI - Data Pusher
Pushes real-time match data to browser overlays at optimal rates.
Manages data channels, throttling, and priority-based updates.
"""

import asyncio
import time
from typing import Dict, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.overlays.data")


class PushRate(str, Enum):
    """Data push rates"""
    REALTIME = "realtime"     # Every frame (30fps)
    FAST = "fast"            # 10 updates/sec
    NORMAL = "normal"        # 5 updates/sec
    SLOW = "slow"            # 2 updates/sec
    LAZY = "lazy"           # 1 update/sec
    ON_CHANGE = "on_change"  # Only when data changes


class DataChannel(str, Enum):
    """Data channels for overlays"""
    SCOREBOARD = "scoreboard"
    KILL_FEED = "kill_feed"
    MINIMAP = "minimap"
    TEAM_STATS = "team_stats"
    PLAYER_STATS = "player_stats"
    ALIVE_COUNT = "alive_count"
    ZONE_INFO = "zone_info"
    COMMENTARY = "commentary"
    WEAPON_INFO = "weapon_info"
    UTILITY_INFO = "utility_info"
    FIGHT_INFO = "fight_info"
    CLUTCH_INFO = "clutch_info"
    MVP_INFO = "mvp_info"
    MATCH_INFO = "match_info"
    PREDICTIONS = "predictions"
    STORYLINES = "storylines"
    SPONSOR = "sponsor"


@dataclass
class DataChannelConfig:
    """Configuration for a data channel"""
    channel: DataChannel
    push_rate: PushRate = PushRate.NORMAL
    rate_limit_ms: int = 200  # Minimum ms between pushes
    priority: int = 50
    last_pushed: float = 0.0
    data_version: int = 0  # Incremented on data change
    subscribers: Set[str] = field(default_factory=set)


@dataclass
class PushMessage:
    """A data push message"""
    channel: DataChannel
    data: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    version: int = 0
    priority: int = 50


class DataPusher:
    """
    Pushes real-time data to overlay clients.
    
    Features:
    - Multiple data channels with different rates
    - Rate limiting and throttling
    - Change detection (only push when data changes)
    - Priority-based push scheduling
    - Data versioning
    - Batch pushing for efficiency
    """
    
    def __init__(self, overlay_server: Any):
        self.server = overlay_server
        
        # Channel configurations
        self.channels: Dict[DataChannel, DataChannelConfig] = {}
        
        # Push queue
        self.push_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
        
        # Recent data cache
        self.data_cache: Dict[DataChannel, Dict[str, Any]] = {}
        
        # Statistics
        self._total_pushes = 0
        self._total_data_sent_mb = 0.0
        
        # Tasks
        self._push_task: Optional[asyncio.Task] = None
        
        # Initialize channels
        self._initialize_channels()
        
        self._log = logger.bind(component="data_pusher")
    
    def _initialize_channels(self) -> None:
        """Initialize data channels with default rates"""
        
        channel_configs = [
            (DataChannel.KILL_FEED, PushRate.FAST, 100, 90),
            (DataChannel.ALIVE_COUNT, PushRate.ON_CHANGE, 500, 85),
            (DataChannel.ZONE_INFO, PushRate.ON_CHANGE, 1000, 80),
            (DataChannel.FIGHT_INFO, PushRate.FAST, 150, 90),
            (DataChannel.CLUTCH_INFO, PushRate.REALTIME, 50, 100),
            (DataChannel.SCOREBOARD, PushRate.NORMAL, 200, 70),
            (DataChannel.TEAM_STATS, PushRate.NORMAL, 500, 60),
            (DataChannel.PLAYER_STATS, PushRate.SLOW, 1000, 50),
            (DataChannel.COMMENTARY, PushRate.ON_CHANGE, 100, 85),
            (DataChannel.MINIMAP, PushRate.FAST, 150, 75),
            (DataChannel.WEAPON_INFO, PushRate.ON_CHANGE, 200, 65),
            (DataChannel.UTILITY_INFO, PushRate.SLOW, 500, 55),
            (DataChannel.MATCH_INFO, PushRate.SLOW, 1000, 45),
            (DataChannel.PREDICTIONS, PushRate.SLOW, 1000, 40),
            (DataChannel.STORYLINES, PushRate.LAZY, 2000, 30),
            (DataChannel.MVP_INFO, PushRate.ON_CHANGE, 500, 75),
            (DataChannel.SPONSOR, PushRate.LAZY, 5000, 10),
        ]
        
        for channel, rate, rate_limit, priority in channel_configs:
            self.channels[channel] = DataChannelConfig(
                channel=channel,
                push_rate=rate,
                rate_limit_ms=rate_limit,
                priority=priority,
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PUSH CONTROL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start(self) -> None:
        """Start the data pusher"""
        self._push_task = asyncio.create_task(self._push_loop())
        self._log.info("Data pusher started")
    
    async def stop(self) -> None:
        """Stop the data pusher"""
        if self._push_task:
            self._push_task.cancel()
            try:
                await self._push_task
            except asyncio.CancelledError:
                pass
        self._log.info("Data pusher stopped")
    
    async def push(self, channel: DataChannel, data: Dict[str, Any]) -> bool:
        """
        Push data to a channel.
        
        Args:
            channel: Data channel to push to
            data: Data to push
            
        Returns:
            True if queued for push, False if throttled
        """
        
        config = self.channels.get(channel)
        if not config:
            self._log.warning(f"Unknown channel: {channel}")
            return False
        
        current_time = time.time()
        
        # Check rate limit
        if config.push_rate != PushRate.REALTIME:
            time_since_last = (current_time - config.last_pushed) * 1000
            
            if time_since_last < config.rate_limit_ms:
                return False  # Throttled
        
        # Check if data actually changed
        if config.push_rate == PushRate.ON_CHANGE:
            cached = self.data_cache.get(channel, {})
            if cached == data:
                return False  # No change
        
        # Update cache
        self.data_cache[channel] = data.copy()
        config.data_version += 1
        config.last_pushed = current_time
        
        # Queue push (priority queue - lower number = higher priority)
        priority = 100 - config.priority  # Invert for min-heap
        message = PushMessage(
            channel=channel,
            data=data,
            version=config.data_version,
            priority=config.priority,
        )
        
        await self.push_queue.put((priority, message))
        
        return True
    
    async def push_batch(self, updates: Dict[DataChannel, Dict[str, Any]]) -> int:
        """Push multiple channel updates at once"""
        
        pushed = 0
        
        for channel, data in updates.items():
            if await self.push(channel, data):
                pushed += 1
        
        return pushed
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PUSH LOOP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _push_loop(self) -> None:
        """Main push loop - processes queued messages"""
        
        while True:
            try:
                # Get next message with timeout
                priority, message = await asyncio.wait_for(
                    self.push_queue.get(), timeout=0.1
                )
                
                # Determine which overlays should receive this
                overlay_type = self._get_overlay_for_channel(message.channel)
                
                if overlay_type:
                    # Send to overlay server
                    await self.server.broadcast_to_type(
                        overlay_type,
                        {
                            "type": "data_update",
                            "channel": message.channel.value,
                            "data": message.data,
                            "version": message.version,
                        },
                        priority=message.priority,
                    )
                    
                    self._total_pushes += 1
                    
                    # Estimate data size
                    import json
                    data_size = len(json.dumps(message.data))
                    self._total_data_sent_mb += data_size / (1024 * 1024)
                
                self.push_queue.task_done()
                
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Push loop error: {e}")
    
    def _get_overlay_for_channel(self, channel: DataChannel) -> Optional[str]:
        """Get the overlay type for a data channel"""
        
        channel_overlay_map = {
            DataChannel.SCOREBOARD: "scoreboard",
            DataChannel.KILL_FEED: "kill_feed",
            DataChannel.MINIMAP: "minimap",
            DataChannel.TEAM_STATS: "team_panel",
            DataChannel.PLAYER_STATS: "player_info",
            DataChannel.ALIVE_COUNT: "alive_counter",
            DataChannel.ZONE_INFO: "zone_indicator",
            DataChannel.COMMENTARY: "commentary_bar",
            DataChannel.WEAPON_INFO: "weapon_display",
            DataChannel.UTILITY_INFO: "utility_count",
            DataChannel.FIGHT_INFO: "scoreboard",
            DataChannel.CLUTCH_INFO: "scoreboard",
            DataChannel.MATCH_INFO: "scoreboard",
            DataChannel.MVP_INFO: "mvp_display",
            DataChannel.SPONSOR: "sponsor_banner",
        }
        
        return channel_overlay_map.get(channel)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PUSH RATE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_push_rate(self, channel: DataChannel, rate: PushRate) -> None:
        """Change push rate for a channel"""
        if channel in self.channels:
            self.channels[channel].push_rate = rate
            
            # Update rate limit
            rate_limits = {
                PushRate.REALTIME: 33,
                PushRate.FAST: 100,
                PushRate.NORMAL: 200,
                PushRate.SLOW: 500,
                PushRate.LAZY: 1000,
                PushRate.ON_CHANGE: 0,
            }
            self.channels[channel].rate_limit_ms = rate_limits.get(rate, 200)
    
    def set_priority(self, channel: DataChannel, priority: int) -> None:
        """Set channel priority"""
        if channel in self.channels:
            self.channels[channel].priority = max(0, min(100, priority))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_channel_stats(self) -> Dict[str, Any]:
        """Get per-channel statistics"""
        return {
            channel.value: {
                "push_rate": config.push_rate.value,
                "rate_limit_ms": config.rate_limit_ms,
                "priority": config.priority,
                "version": config.data_version,
                "last_pushed": config.last_pushed,
            }
            for channel, config in self.channels.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get data pusher statistics"""
        return {
            "total_pushes": self._total_pushes,
            "total_data_mb": round(self._total_data_sent_mb, 2),
            "queue_size": self.push_queue.qsize(),
            "channels": len(self.channels),
            "channel_stats": self.get_channel_stats(),
        }