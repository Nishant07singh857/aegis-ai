# backend/app/vision/capture/feed_health_monitor.py
"""
AEGIS AI - Feed Health Monitor
Monitors quality of video feeds from all spectator devices.
Detects frame drops, artifacts, lag, and feed degradation.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.vision.feed_health")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HealthStatus(str, Enum):
    """Feed health status"""
    EXCELLENT = "excellent"     # Perfect feed
    GOOD = "good"              # Minor issues
    FAIR = "fair"              # Noticeable issues
    POOR = "poor"              # Significant degradation
    CRITICAL = "critical"      # Feed unusable
    OFFLINE = "offline"        # No feed

@dataclass
class FeedHealth:
    """Health status of a single feed"""
    source_id: str
    status: HealthStatus = HealthStatus.GOOD
    health_score: float = 100.0  # 0-100
    
    # Frame metrics
    expected_fps: float = 30.0
    actual_fps: float = 30.0
    fps_stability: float = 100.0  # How stable FPS is (0-100)
    frame_drops_last_minute: int = 0
    total_frame_drops: int = 0
    
    # Latency
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    latency_spikes: int = 0
    
    # Quality
    resolution_match: bool = True
    bitrate_adequate: bool = True
    artifact_count: int = 0
    black_frame_count: int = 0
    frozen_frame_count: int = 0
    
    # Connection
    reconnect_count: int = 0
    last_error: str = ""
    last_error_time: float = 0.0
    
    # Timing
    last_frame_time: float = 0.0
    last_health_check: float = field(default_factory=time.time)
    uptime_seconds: float = 0.0
    
    def get_status_color(self) -> str:
        """Get color code for status"""
        colors = {
            HealthStatus.EXCELLENT: "🟢",
            HealthStatus.GOOD: "🟢",
            HealthStatus.FAIR: "🟡",
            HealthStatus.POOR: "🟠",
            HealthStatus.CRITICAL: "🔴",
            HealthStatus.OFFLINE: "⚫",
        }
        return colors.get(self.status, "⚪")


class FeedHealthMonitor:
    """
    Monitors health of all video feeds.
    
    Tracks:
    - Frame rate and stability
    - Feed latency and spikes
    - Resolution and bitrate adequacy
    - Frame artifacts and quality
    - Connection stability
    - Feed uptime
    
    Provides alerts for degraded feeds.
    """
    
    def __init__(self):
        # Feed health records
        self.feeds: Dict[str, FeedHealth] = {}
        
        # Frame timing history for FPS calculation
        self.frame_times: Dict[str, deque] = {}
        
        # Alert history
        self.alerts: deque = deque(maxlen=100)
        
        # Thresholds
        self.thresholds = {
            "min_acceptable_fps": 15,
            "max_acceptable_latency_ms": 500,
            "fps_stability_threshold": 80,
            "artifact_threshold_per_minute": 10,
            "max_reconnect_before_alert": 3,
            "frame_timeout_seconds": 5,  # No frame for this long = offline
        }
        
        self._log = logger.bind(component="feed_health")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEALTH MONITORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def register_feed(self, source_id: str, expected_fps: float = 30.0) -> FeedHealth:
        """Register a new feed for monitoring"""
        
        health = FeedHealth(
            source_id=source_id,
            expected_fps=expected_fps,
        )
        
        self.feeds[source_id] = health
        self.frame_times[source_id] = deque(maxlen=100)
        
        self._log.info(f"Feed registered: {source_id}")
        
        return health
    
    def unregister_feed(self, source_id: str) -> bool:
        """Remove a feed from monitoring"""
        if source_id in self.feeds:
            del self.feeds[source_id]
            del self.frame_times[source_id]
            return True
        return False
    
    def record_frame(self, source_id: str, timestamp: float = None) -> None:
        """Record a frame received from a feed"""
        
        if source_id not in self.feeds:
            return
        
        ts = timestamp or time.time()
        self.frame_times[source_id].append(ts)
        
        health = self.feeds[source_id]
        health.last_frame_time = ts
    
    def record_frame_drop(self, source_id: str) -> None:
        """Record a dropped frame"""
        if source_id in self.feeds:
            self.feeds[source_id].frame_drops_last_minute += 1
            self.feeds[source_id].total_frame_drops += 1
    
    def record_artifact(self, source_id: str) -> None:
        """Record a frame artifact"""
        if source_id in self.feeds:
            self.feeds[source_id].artifact_count += 1
    
    def record_latency(self, source_id: str, latency_ms: float) -> None:
        """Record frame latency"""
        if source_id in self.feeds:
            health = self.feeds[source_id]
            
            # Update average
            n = max(1, health.total_frame_drops + 1)
            health.avg_latency_ms = (
                health.avg_latency_ms * (n - 1) + latency_ms
            ) / n
            
            # Track max
            health.max_latency_ms = max(health.max_latency_ms, latency_ms)
            
            # Track spikes (latency > 3x average)
            if latency_ms > health.avg_latency_ms * 3:
                health.latency_spikes += 1
    
    def record_error(self, source_id: str, error: str) -> None:
        """Record a feed error"""
        if source_id in self.feeds:
            health = self.feeds[source_id]
            health.last_error = error
            health.last_error_time = time.time()
            health.reconnect_count += 1
    
    def update_health(self, source_id: str) -> FeedHealth:
        """Update health assessment for a feed"""
        
        health = self.feeds.get(source_id)
        if not health:
            return None
        
        health.last_health_check = time.time()
        
        # Calculate actual FPS
        frame_times = list(self.frame_times.get(source_id, []))
        
        if len(frame_times) >= 2:
            time_span = frame_times[-1] - frame_times[0]
            if time_span > 0:
                health.actual_fps = (len(frame_times) - 1) / time_span
        
        # Check if feed is alive
        time_since_last_frame = time.time() - health.last_frame_time
        if time_since_last_frame > self.thresholds["frame_timeout_seconds"]:
            health.status = HealthStatus.OFFLINE
            health.health_score = 0
            return health
        
        # Calculate health score
        score = 100.0
        
        # FPS check
        if health.actual_fps < self.thresholds["min_acceptable_fps"]:
            score -= 30
        elif health.actual_fps < health.expected_fps * 0.7:
            score -= 15
        elif health.actual_fps < health.expected_fps * 0.9:
            score -= 5
        
        # Latency check
        if health.avg_latency_ms > self.thresholds["max_acceptable_latency_ms"]:
            score -= 25
        elif health.avg_latency_ms > 200:
            score -= 10
        
        # Frame drops
        if health.frame_drops_last_minute > 30:
            score -= 25
        elif health.frame_drops_last_minute > 10:
            score -= 10
        
        # Artifacts
        if health.artifact_count > self.thresholds["artifact_threshold_per_minute"]:
            score -= 15
        
        # Reconnects
        if health.reconnect_count > self.thresholds["max_reconnect_before_alert"]:
            score -= 20
        
        health.health_score = max(0, score)
        
        # Determine status
        if score >= 90:
            health.status = HealthStatus.EXCELLENT
        elif score >= 70:
            health.status = HealthStatus.GOOD
        elif score >= 50:
            health.status = HealthStatus.FAIR
        elif score >= 30:
            health.status = HealthStatus.POOR
        else:
            health.status = HealthStatus.CRITICAL
        
        # Reset per-minute counters
        health.frame_drops_last_minute = 0
        health.artifact_count = 0
        
        # Check for alerts
        if health.status in [HealthStatus.POOR, HealthStatus.CRITICAL]:
            self._add_alert(source_id, health)
        
        return health
    
    def _add_alert(self, source_id: str, health: FeedHealth) -> None:
        """Add a health alert"""
        alert = {
            "source_id": source_id,
            "status": health.status.value,
            "health_score": round(health.health_score, 1),
            "actual_fps": round(health.actual_fps, 1),
            "latency_ms": round(health.avg_latency_ms, 1),
            "frame_drops": health.total_frame_drops,
            "timestamp": time.time(),
        }
        
        self.alerts.append(alert)
        
        self._log.warning(
            f"Feed health alert: {source_id}",
            status=health.status.value,
            score=round(health.health_score, 1),
        )
    
    def update_all(self) -> Dict[str, FeedHealth]:
        """Update health for all feeds"""
        for source_id in self.feeds:
            self.update_health(source_id)
        return self.feeds
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_feed_health(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Get health report for a specific feed"""
        health = self.feeds.get(source_id)
        if not health:
            return None
        
        return {
            "source_id": health.source_id,
            "status": health.status.value,
            "health_score": round(health.health_score, 1),
            "fps": {
                "expected": health.expected_fps,
                "actual": round(health.actual_fps, 1),
                "stability": round(health.fps_stability, 1),
            },
            "latency": {
                "avg_ms": round(health.avg_latency_ms, 1),
                "max_ms": round(health.max_latency_ms, 1),
                "spikes": health.latency_spikes,
            },
            "quality": {
                "resolution_match": health.resolution_match,
                "bitrate_adequate": health.bitrate_adequate,
                "frame_drops": health.total_frame_drops,
                "artifacts": health.artifact_count,
            },
            "connection": {
                "reconnects": health.reconnect_count,
                "last_error": health.last_error,
                "last_frame_ago": round(time.time() - health.last_frame_time, 2),
            },
            "status_color": health.get_status_color(),
        }
    
    def get_all_health(self) -> Dict[str, Any]:
        """Get health for all feeds"""
        return {
            source_id: self.get_feed_health(source_id)
            for source_id in self.feeds
        }
    
    def get_unhealthy_feeds(self) -> List[str]:
        """Get feeds that need attention"""
        return [
            source_id for source_id, health in self.feeds.items()
            if health.status in [HealthStatus.POOR, HealthStatus.CRITICAL, HealthStatus.OFFLINE]
        ]
    
    def get_recent_alerts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent health alerts"""
        return list(self.alerts)[-limit:]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get feed health monitor statistics"""
        self.update_all()
        
        feeds = list(self.feeds.values())
        
        return {
            "total_feeds": len(feeds),
            "healthy": len([f for f in feeds if f.status in [HealthStatus.EXCELLENT, HealthStatus.GOOD]]),
            "unhealthy": len([f for f in feeds if f.status in [HealthStatus.POOR, HealthStatus.CRITICAL]]),
            "offline": len([f for f in feeds if f.status == HealthStatus.OFFLINE]),
            "average_health": round(
                sum(f.health_score for f in feeds) / max(1, len(feeds)), 1
            ),
            "feeds": self.get_all_health(),
            "alerts": self.get_recent_alerts(5),
        }
    
    def reset(self) -> None:
        """Reset feed health monitor"""
        self.feeds.clear()
        self.frame_times.clear()
        self.alerts.clear()
        self._log.info("Feed health monitor reset")