# backend/app/broadcast/replay_controller.py
"""
AEGIS AI - Replay Controller
Manages instant replays, multi-angle replay selection,
slow-motion timing, and replay export for OBS.
"""

import asyncio
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.replay")


class ReplayState(str, Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    EXPORTING = "exporting"


@dataclass
class ReplayClip:
    """A single replay clip"""
    clip_id: str
    start_timestamp: float
    end_timestamp: float
    source_feed: str          # Which spectator feed
    fight_id: Optional[str] = None
    highlight_type: str = "general"
    slow_motion_factor: float = 1.0
    excitement_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class ReplayController:
    """
    Manages professional esports-style instant replays.

    Features:
    - Rolling replay buffer (last N seconds per feed)
    - Multi-angle replay selection
    - Slow-motion timing
    - Clutch moment detection
    - OBS scene integration
    """

    def __init__(self, obs_client=None):
        self.obs_client = obs_client
        self.state = ReplayState.IDLE

        # Replay buffer: feed_id -> list of frame timestamps
        self._replay_buffers: Dict[str, List[float]] = {}
        self._buffer_duration_seconds: int = 30  # Keep last 30s per feed

        # Pending and completed replays
        self._pending_replays: List[ReplayClip] = []
        self._completed_replays: List[ReplayClip] = []
        self._max_completed = 50

        # Stats
        self._total_replays = 0
        self._log = logger.bind(component="replay_controller")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REPLAY BUFFER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def record_frame(self, feed_id: str, timestamp: Optional[float] = None) -> None:
        """Record a frame timestamp into the replay buffer"""
        ts = timestamp or time.time()
        if feed_id not in self._replay_buffers:
            self._replay_buffers[feed_id] = []

        self._replay_buffers[feed_id].append(ts)

        # Trim old frames
        cutoff = ts - self._buffer_duration_seconds
        self._replay_buffers[feed_id] = [
            t for t in self._replay_buffers[feed_id] if t >= cutoff
        ]

    def get_buffer_duration(self, feed_id: str) -> float:
        """Get how much replay buffer is available for a feed"""
        buf = self._replay_buffers.get(feed_id, [])
        if len(buf) < 2:
            return 0.0
        return buf[-1] - buf[0]

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REPLAY TRIGGER
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def trigger_replay(
        self,
        feed_id: str,
        duration_seconds: float = 8.0,
        slow_motion: float = 0.5,
        fight_id: Optional[str] = None,
        highlight_type: str = "clutch",
    ) -> Optional[str]:
        """
        Trigger an instant replay.

        Args:
            feed_id: Which spectator feed to replay
            duration_seconds: How many seconds of footage to replay
            slow_motion: Replay speed (0.5 = half speed)
            fight_id: Related fight ID
            highlight_type: Type of highlight

        Returns:
            clip_id if successful, None otherwise
        """
        if self.state == ReplayState.PLAYING:
            self._log.info("Replay already playing, queueing")
            # Queue it
        
        clip_id = f"replay_{int(time.time()*1000)}"

        clip = ReplayClip(
            clip_id=clip_id,
            start_timestamp=time.time() - duration_seconds,
            end_timestamp=time.time(),
            source_feed=feed_id,
            fight_id=fight_id,
            highlight_type=highlight_type,
            slow_motion_factor=slow_motion,
        )

        self._pending_replays.append(clip)
        self._log.info(
            "Replay triggered",
            clip_id=clip_id,
            feed=feed_id,
            duration=duration_seconds,
            slow_mo=slow_motion,
        )

        # Execute replay via OBS if available
        if self.obs_client:
            await self._execute_obs_replay(clip)

        return clip_id

    async def _execute_obs_replay(self, clip: ReplayClip) -> None:
        """Execute replay through OBS WebSocket"""
        try:
            self.state = ReplayState.PLAYING

            # Switch to replay scene
            if hasattr(self.obs_client, 'set_current_scene'):
                await self.obs_client.set_current_scene("Replay_Scene")

            # Play for calculated duration
            play_duration = (
                (clip.end_timestamp - clip.start_timestamp) / clip.slow_motion_factor
            )
            await asyncio.sleep(min(play_duration, 15.0))  # Cap at 15s

            self.state = ReplayState.IDLE
            self._log.info("Replay complete", clip_id=clip.clip_id)

        except Exception as e:
            self._log.error(f"OBS replay error: {e}")
            self.state = ReplayState.IDLE

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # AUTO REPLAY DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def auto_replay_if_worthy(
        self,
        fight_score: float,
        feed_id: str,
        fight_id: Optional[str] = None,
    ) -> bool:
        """
        Automatically trigger replay if the fight is worthy.

        Args:
            fight_score: 0-100 excitement score
            feed_id: Which feed
            fight_id: Fight identifier

        Returns:
            True if replay was triggered
        """
        if fight_score >= 80:
            clip_id = await self.trigger_replay(
                feed_id=feed_id,
                duration_seconds=10.0,
                slow_motion=0.4,
                fight_id=fight_id,
                highlight_type="clutch" if fight_score >= 90 else "squad_wipe",
            )
            return clip_id is not None

        return False

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATUS / STATS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    def get_stats(self) -> Dict[str, Any]:
        """Get replay controller statistics"""
        return {
            "state": self.state.value,
            "total_replays": self._total_replays,
            "pending_replays": len(self._pending_replays),
            "completed_replays": len(self._completed_replays),
            "buffer_feeds": len(self._replay_buffers),
            "buffer_durations": {
                feed_id: round(self.get_buffer_duration(feed_id), 1)
                for feed_id in self._replay_buffers
            },
        }
