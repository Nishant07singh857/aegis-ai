# backend/app/agents/highlight_agents/content_publisher.py
"""
AEGIS AI - Content Publisher
Automatically publishes highlights to social media platforms.
Handles formatting, scheduling, and cross-platform distribution.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.publisher")

class Platform(str, Enum):
    """Supported publishing platforms"""
    YOUTUBE_SHORTS = "youtube_shorts"
    YOUTUBE_FULL = "youtube_full"
    TIKTOK = "tiktok"
    INSTAGRAM_REELS = "instagram_reels"
    TWITTER = "twitter"
    DISCORD = "discord"
    FACEBOOK = "facebook"

class PublishStatus(str, Enum):
    """Publishing status"""
    QUEUED = "queued"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"

@dataclass
class PublishJob:
    """A content publishing job"""
    job_id: str
    clip_id: str
    platform: Platform
    status: PublishStatus = PublishStatus.QUEUED
    
    # Content
    title: str = ""
    description: str = ""
    hashtags: List[str] = field(default_factory=list)
    
    # Media
    video_path: str = ""
    thumbnail_path: str = ""
    duration_seconds: float = 0.0
    
    # Scheduling
    created_at: float = field(default_factory=time.time)
    scheduled_at: Optional[float] = None
    published_at: Optional[float] = None
    
    # Results
    publish_url: str = ""
    views: int = 0
    likes: int = 0
    error_message: str = ""


class ContentPublisher(BaseAgent):
    """
    Automatically publishes highlights to social media.
    
    Features:
    - Multi-platform publishing
    - Platform-specific formatting
    - Optimal scheduling
    - Hashtag optimization
    - Performance tracking
    - Rate limiting
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Publishing queue
        self.jobs: Dict[str, PublishJob] = {}
        self.publish_queue: deque = deque(maxlen=100)
        
        # Platform limits
        self.platform_limits = {
            Platform.YOUTUBE_SHORTS: {"max_duration": 60, "max_hashtags": 5, "aspect_ratio": "9:16"},
            Platform.TIKTOK: {"max_duration": 180, "max_hashtags": 10, "aspect_ratio": "9:16"},
            Platform.INSTAGRAM_REELS: {"max_duration": 90, "max_hashtags": 30, "aspect_ratio": "9:16"},
            Platform.YOUTUBE_FULL: {"max_duration": 600, "max_hashtags": 15, "aspect_ratio": "16:9"},
            Platform.TWITTER: {"max_duration": 140, "max_hashtags": 3, "aspect_ratio": "16:9"},
            Platform.DISCORD: {"max_duration": 600, "max_hashtags": 0, "aspect_ratio": "any"},
        }
        
        # Publishing statistics
        self.publish_stats: Dict[Platform, Dict[str, int]] = {
            p: {"published": 0, "failed": 0, "total_views": 0}
            for p in Platform
        }
        
        self._log = logger.bind(component="content_publisher")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PUBLISHING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def queue_publish(
        self,
        clip_data: Dict[str, Any],
        platforms: List[Platform],
        scheduled_at: Optional[float] = None,
    ) -> List[str]:
        """Queue a clip for publishing to multiple platforms"""
        
        job_ids = []
        clip_id = clip_data.get("clip_id", "")
        
        for platform in platforms:
            # Check platform compatibility
            if not self._is_platform_compatible(clip_data, platform):
                continue
            
            # Create job
            job = PublishJob(
                job_id=f"pub_{int(time.time())}_{platform.value}_{clip_id[:8]}",
                clip_id=clip_id,
                platform=platform,
                title=self._format_title(clip_data, platform),
                description=self._format_description(clip_data, platform),
                hashtags=self._format_hashtags(clip_data, platform),
                duration_seconds=clip_data.get("duration_seconds", 30),
                scheduled_at=scheduled_at,
            )
            
            if scheduled_at:
                job.status = PublishStatus.SCHEDULED
            
            self.jobs[job.job_id] = job
            self.publish_queue.append(job)
            job_ids.append(job.job_id)
            
            self._log.info(
                f"Queued for {platform.value}: {clip_data.get('title', 'Untitled')}",
                scheduled=scheduled_at is not None,
            )
        
        return job_ids
    
    def publish_best_to_all(
        self,
        clip_data: Dict[str, Any],
        min_excitement: float = 70.0,
    ) -> List[str]:
        """Publish a clip to all compatible platforms if exciting enough"""
        
        excitement = clip_data.get("excitement_score", 0)
        
        if excitement < min_excitement:
            self._log.debug(
                f"Clip below excitement threshold ({excitement:.0f} < {min_excitement:.0f})"
            )
            return []
        
        # Get platform suitability
        platform_scores = clip_data.get("platform_suitability", {})
        
        # Select platforms where score > 60
        suitable_platforms = []
        for platform in Platform:
            score = platform_scores.get(platform.value, 50)
            if score > 60:
                suitable_platforms.append(platform)
        
        if not suitable_platforms:
            # Default platforms
            suitable_platforms = [Platform.YOUTUBE_SHORTS, Platform.INSTAGRAM_REELS]
        
        return self.queue_publish(clip_data, suitable_platforms)
    
    def _is_platform_compatible(
        self,
        clip_data: Dict[str, Any],
        platform: Platform,
    ) -> bool:
        """Check if clip is compatible with platform"""
        
        limits = self.platform_limits.get(platform, {})
        duration = clip_data.get("duration_seconds", 60)
        
        if duration > limits.get("max_duration", 60):
            return False
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FORMATTING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _format_title(self, clip_data: Dict[str, Any], platform: Platform) -> str:
        """Format title for platform"""
        
        title = clip_data.get("title", "BGMI Highlight")
        
        # Platform-specific formatting
        if platform == Platform.TIKTOK:
            # Shorter, more engaging titles
            return title[:50]
        elif platform == Platform.YOUTUBE_SHORTS:
            # Add emoji
            return f"🔥 {title[:80]}"
        elif platform == Platform.INSTAGRAM_REELS:
            return title[:60]
        elif platform == Platform.TWITTER:
            return title[:40]
        
        return title
    
    def _format_description(self, clip_data: Dict[str, Any], platform: Platform) -> str:
        """Format description for platform"""
        
        description = clip_data.get("description", "")
        player = clip_data.get("primary_player", "")
        team = clip_data.get("primary_team", "")
        
        parts = []
        
        if platform == Platform.YOUTUBE_FULL:
            parts.append(description)
            if player:
                parts.append(f"\n\nPlayer: {player}")
            if team:
                parts.append(f"Team: {team}")
            parts.append("\n\n#BGMI #Esports #Gaming")
        elif platform == Platform.INSTAGRAM_REELS:
            parts.append(description[:100])
            parts.append("\n.\n.\n.")
        else:
            parts.append(description[:50])
        
        return " ".join(parts)
    
    def _format_hashtags(
        self,
        clip_data: Dict[str, Any],
        platform: Platform,
    ) -> List[str]:
        """Format hashtags for platform"""
        
        hashtags = clip_data.get("hashtags", ["#BGMI", "#Esports"])
        limits = self.platform_limits.get(platform, {})
        max_tags = limits.get("max_hashtags", 5)
        
        # Add platform-specific tags
        platform_tags = {
            Platform.TIKTOK: ["#fyp", "#gaming", "#viral"],
            Platform.INSTAGRAM_REELS: ["#reels", "#gaming", "#bgmiindia"],
            Platform.YOUTUBE_SHORTS: ["#shorts", "#gaming"],
        }
        
        extra_tags = platform_tags.get(platform, [])
        
        # Combine and limit
        all_tags = list(set(hashtags + extra_tags))
        return all_tags[:max_tags]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_pending_jobs(self) -> List[Dict[str, Any]]:
        """Get pending publishing jobs"""
        return [
            {
                "job_id": j.job_id,
                "platform": j.platform.value,
                "title": j.title,
                "status": j.status.value,
                "scheduled_at": j.scheduled_at,
            }
            for j in self.publish_queue
            if j.status in [PublishStatus.QUEUED, PublishStatus.SCHEDULED]
        ]
    
    def get_platform_stats(self) -> Dict[str, Any]:
        """Get publishing statistics per platform"""
        return {
            p.value: stats
            for p, stats in self.publish_stats.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get content publisher statistics"""
        return {
            "total_jobs": len(self.jobs),
            "pending_jobs": len(self.get_pending_jobs()),
            "by_platform": self.get_platform_stats(),
        }
    
    def reset(self) -> None:
        """Reset content publisher"""
        self.jobs.clear()
        self.publish_queue.clear()
        self._log.info("Content publisher reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("highlight_moment")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        The heuristic engine is driven synchronously via direct method
        calls; this hook keeps match context current for future
        event-driven use.
        """
        try:
            data = record.event.data if hasattr(record, "event") else {}
            if isinstance(data, dict) and data.get("match_id"):
                self.config.match_id = data["match_id"]
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
