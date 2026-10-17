# backend/app/agents/highlight_agents/clip_detector.py
"""
AEGIS AI - Clip Detector
Automatically detects highlight-worthy moments for clipping.
Identifies squad wipes, clutch plays, multi-kills, and funny moments.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventPriority, EventRecord

logger = structlog.get_logger("aegis.agents.clip_detector")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HighlightCategory(str, Enum):
    """Categories of highlight clips"""
    SQUAD_WIPE = "squad_wipe"           # Full team elimination
    CLUTCH_WIN = "clutch_win"           # 1vX victory
    MULTI_KILL = "multi_kill"           # 3+ kills in quick succession
    GRENADE_KILL = "grenade_kill"       # Impressive grenade
    LONG_RANGE = "long_range"           # 200m+ kill
    HEADSHOT_CHAIN = "headshot_chain"   # Multiple headshots
    FINAL_CIRCLE = "final_circle"       # Final circle action
    FUNNY_MOMENT = "funny_moment"       # Humorous events
    COMEBACK = "comeback"              # Comeback victory
    RECORD = "record"                  # Record-breaking moment
    TEAM_WIPE_DENIAL = "team_wipe_denial"  # Preventing team wipe

class ClipQuality(str, Enum):
    """Quality assessment of clip"""
    LEGENDARY = "legendary"     # All-time great
    EXCELLENT = "excellent"     # Must-share
    GOOD = "good"              # Worth saving
    DECENT = "decent"          # Nice moment
    AVERAGE = "average"        # Standard play

@dataclass
class HighlightClip:
    """A detected highlight clip"""
    clip_id: str
    category: HighlightCategory
    quality: ClipQuality = ClipQuality.GOOD
    
    # Content
    title: str = ""
    description: str = ""
    
    # Timing
    start_timestamp_ms: float = 0.0
    end_timestamp_ms: float = 0.0
    duration_seconds: float = 0.0
    detected_at: float = field(default_factory=time.time)
    
    # Participants
    primary_player: str = ""
    primary_team: str = ""
    secondary_players: List[str] = field(default_factory=list)
    secondary_teams: List[str] = field(default_factory=list)
    
    # Metrics
    excitement_score: float = 0.0
    viral_potential: float = 0.0  # 0-100
    replay_value: float = 0.0
    
    # Source
    match_id: str = ""
    fight_id: str = ""
    event_ids: List[str] = field(default_factory=list)
    
    # Production
    suggested_angles: List[str] = field(default_factory=list)
    suggested_speed: float = 1.0  # 1.0 = normal, 0.25 = slow-mo
    needs_slow_motion: bool = False
    needs_zoom: bool = False
    
    # Social media
    platform_suitability: Dict[str, float] = field(default_factory=dict)
    hashtags: List[str] = field(default_factory=list)
    thumbnail_timestamp_ms: float = 0.0
    
    # Processing
    is_processed: bool = False
    processed_path: str = ""
    thumbnail_path: str = ""


class ClipDetector(BaseAgent):
    """
    Detects highlight-worthy moments in real-time.
    
    Detection Categories:
    1. Squad Wipes - Full team eliminations
    2. Clutch Wins - 1vX victories
    3. Multi-Kills - 3+ kills in quick succession
    4. Grenade Kills - Impressive explosive plays
    5. Long Range Kills - 200m+ snipes
    6. Headshot Chains - Multiple headshots
    7. Final Circle - Decisive final moments
    8. Funny Moments - Unusual/humorous events
    
    Each clip is scored for:
    - Excitement value
    - Viral potential
    - Platform suitability (YouTube, TikTok, Instagram)
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Detected clips
        self.clips: Dict[str, HighlightClip] = {}
        self.clip_history: deque = deque(maxlen=100)
        
        # Detection buffers
        self.recent_kills: deque = deque(maxlen=20)
        self.recent_damage: deque = deque(maxlen=50)
        
        # Scoring weights
        self.quality_thresholds = {
            ClipQuality.LEGENDARY: 90,
            ClipQuality.EXCELLENT: 75,
            ClipQuality.GOOD: 55,
            ClipQuality.DECENT: 35,
            ClipQuality.AVERAGE: 15,
        }
        
        # Platform suitability factors
        self.platform_factors = {
            "youtube_shorts": {"max_duration": 60, "aspect_ratio": "9:16", "preferred_categories": ["clutch_win", "multi_kill", "squad_wipe"]},
            "tiktok": {"max_duration": 180, "aspect_ratio": "9:16", "preferred_categories": ["funny_moment", "clutch_win", "grenade_kill"]},
            "instagram_reels": {"max_duration": 90, "aspect_ratio": "9:16", "preferred_categories": ["clutch_win", "multi_kill", "headshot_chain"]},
            "youtube_full": {"max_duration": 600, "aspect_ratio": "16:9", "preferred_categories": ["squad_wipe", "final_circle", "comeback"]},
        }
        
        self._log = logger.bind(component="clip_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLIP DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect_clip(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        match_context: Dict[str, Any],
        fight_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[HighlightClip]:
        """Detect if an event should become a highlight clip"""
        
        clip = None
        
        if event_type == "squad_wipe":
            clip = self._create_squad_wipe_clip(event_data, match_context)
        elif event_type == "clutch_win":
            clip = self._create_clutch_clip(event_data, match_context)
        elif event_type == "multi_kill":
            clip = self._create_multi_kill_clip(event_data, match_context)
        elif event_type == "grenade_kill":
            clip = self._create_grenade_clip(event_data, match_context)
        elif event_type == "long_range_kill":
            clip = self._create_long_range_clip(event_data, match_context)
        elif event_type == "headshot_chain":
            clip = self._create_headshot_clip(event_data, match_context)
        elif event_type == "final_circle_moment":
            clip = self._create_final_circle_clip(event_data, match_context)
        elif event_type == "funny_moment":
            clip = self._create_funny_clip(event_data, match_context)
        elif event_type == "comeback_win":
            clip = self._create_comeback_clip(event_data, match_context)
        
        if clip:
            self._process_clip(clip)
        
        return clip
    
    def _create_squad_wipe_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create squad wipe highlight clip"""
        
        team_name = data.get("team_name", "Unknown")
        eliminator = data.get("eliminator", "Unknown")
        time_span = data.get("time_span", 15)
        placement = data.get("placement", 16)
        
        excitement = 65
        if placement <= 3:
            excitement += 25
        if time_span < 10:
            excitement += 10
        
        quality = self._determine_quality(excitement)
        
        clip = HighlightClip(
            clip_id=f"clip_wipe_{int(time.time())}",
            category=HighlightCategory.SQUAD_WIPE,
            quality=quality,
            title=f"SQUAD WIPE! {eliminator} eliminates {team_name}",
            description=f"{eliminator} wipes out {team_name} in {time_span:.1f}s at #{placement} place!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 5000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 3000,
            duration_seconds=time_span + 8,
            primary_team=eliminator,
            secondary_teams=[team_name],
            excitement_score=excitement,
            viral_potential=min(95, excitement + 15),
            replay_value=excitement,
            match_id=context.get("match_id", ""),
            fight_id=data.get("fight_id", ""),
            suggested_angles=["eliminator_pov", "overhead", "victim_pov"],
            needs_slow_motion=(quality in [ClipQuality.LEGENDARY, ClipQuality.EXCELLENT]),
            hashtags=["#SquadWipe", "#BGMI", "#Esports"],
        )
        
        return clip
    
    def _create_clutch_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create clutch win highlight clip"""
        
        player_name = data.get("player_name", "Unknown")
        enemies = data.get("enemy_count", 4)
        probability = data.get("win_probability", 0.1)
        
        excitement = 80
        if enemies >= 4:
            excitement += 15
        if probability < 0.1:
            excitement += 5
        
        quality = self._determine_quality(excitement)
        
        return HighlightClip(
            clip_id=f"clip_clutch_{int(time.time())}",
            category=HighlightCategory.CLUTCH_WIN,
            quality=quality,
            title=f"IMPOSSIBLE! {player_name} wins 1v{enemies} Clutch!",
            description=f"{player_name} defies {probability:.0%} odds to win a 1v{enemies} clutch! LEGENDARY!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 8000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 5000,
            duration_seconds=20,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(100, excitement + 20),
            replay_value=100,
            match_id=context.get("match_id", ""),
            suggested_angles=["clutch_player_pov", "enemy_pov", "overhead"],
            suggested_speed=0.5,
            needs_slow_motion=True,
            hashtags=["#Clutch", "#1v4", "#BGMI", "#Impossible"],
            platform_suitability={
                "youtube_shorts": 95,
                "tiktok": 98,
                "instagram_reels": 95,
                "youtube_full": 85,
            },
        )
    
    def _create_multi_kill_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create multi-kill highlight clip"""
        
        player_name = data.get("player_name", "Unknown")
        kill_count = data.get("kill_count", 3)
        time_span = data.get("time_span", 10)
        
        excitement = 50 + kill_count * 10
        if time_span < 5:
            excitement += 10
        
        quality = self._determine_quality(excitement)
        
        return HighlightClip(
            clip_id=f"clip_multi_{int(time.time())}",
            category=HighlightCategory.MULTI_KILL,
            quality=quality,
            title=f"RAMPAGE! {player_name} gets {kill_count} kills in {time_span:.1f}s!",
            description=f"{player_name} goes on a rampage with {kill_count} kills in just {time_span:.1f} seconds!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 3000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 2000,
            duration_seconds=time_span + 5,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(90, excitement + 10),
            replay_value=excitement - 10,
            match_id=context.get("match_id", ""),
            suggested_angles=["player_pov", "follow_action"],
            hashtags=["#MultiKill", "#Rampage", "#BGMI"],
        )
    
    def _create_grenade_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create grenade kill highlight"""
        
        player_name = data.get("player_name", "Unknown")
        victims = data.get("victims", 1)
        distance = data.get("distance", 0)
        
        excitement = 45 + victims * 15
        if distance > 50:
            excitement += 10
        
        quality = self._determine_quality(excitement)
        
        return HighlightClip(
            clip_id=f"clip_nade_{int(time.time())}",
            category=HighlightCategory.GRENADE_KILL,
            quality=quality,
            title=f"KOBE! {player_name} with the {victims}-man grenade!",
            description=f"Perfect grenade from {player_name} takes out {victims} players!",
            start_timestamp_ms=data.get("timestamp_ms", 0) - 5000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 3000,
            duration_seconds=8,
            primary_player=player_name,
            primary_team=data.get("team_name", ""),
            excitement_score=excitement,
            viral_potential=min(85, excitement + 15),
            replay_value=excitement,
            match_id=context.get("match_id", ""),
            suggested_angles=["player_pov", "overhead", "explosion_closeup"],
            needs_slow_motion=(victims >= 3),
            hashtags=["#GrenadeKill", "#Nade", "#BGMI"],
            platform_suitability={"tiktok": 90, "youtube_shorts": 85},
        )
    
    def _create_long_range_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create long range kill highlight"""
        
        player_name = data.get("player_name", "Unknown")
        distance = data.get("distance", 200)
        weapon = data.get("weapon", "Sniper")
        headshot = data.get("headshot", False)
        
        excitement = 40 + min(40, distance / 10)
        if headshot:
            excitement += 15
        
        quality = self._determine_quality(excitement)
        
        return HighlightClip(
            clip_id=f"clip_range_{int(time.time())}",
            category=HighlightCategory.LONG_RANGE,
            quality=quality,
            title=f"INCREDIBLE! {player_name} hits {distance}m {weapon} shot!",
            description=f"{player_name} lands an unbelievable {distance} meter {'headshot' if headshot else 'kill'} with {weapon}!",
            start_timestamp_ms=data.get("timestamp_ms", 0) - 4000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 3000,
            duration_seconds=7,
            primary_player=player_name,
            excitement_score=excitement,
            viral_potential=min(80, excitement),
            replay_value=excitement,
            suggested_angles=["player_scope_view", "bullet_trajectory", "victim_pov"],
            needs_slow_motion=True,
            needs_zoom=True,
            hashtags=["#LongRange", "#Sniper", "#BGMI"],
        )
    
    def _create_headshot_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create headshot chain highlight"""
        
        player_name = data.get("player_name", "Unknown")
        headshots = data.get("headshots", 2)
        
        excitement = 40 + headshots * 15
        
        return HighlightClip(
            clip_id=f"clip_hs_{int(time.time())}",
            category=HighlightCategory.HEADSHOT_CHAIN,
            quality=self._determine_quality(excitement),
            title=f"ONE TAPS! {player_name} with {headshots} headshots!",
            description=f"Clean headshots from {player_name} - {headshots} perfect one-taps!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 2000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 2000,
            duration_seconds=6,
            primary_player=player_name,
            excitement_score=excitement,
            viral_potential=min(75, excitement + 10),
            suggested_angles=["player_pov", "scope_view"],
            needs_slow_motion=True,
            hashtags=["#Headshot", "#OneTap", "#BGMI"],
        )
    
    def _create_final_circle_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create final circle highlight"""
        
        winner = data.get("winner_team", "Unknown")
        teams_alive = data.get("teams_alive", 2)
        
        excitement = 70
        if teams_alive <= 2:
            excitement += 20
        
        return HighlightClip(
            clip_id=f"clip_final_{int(time.time())}",
            category=HighlightCategory.FINAL_CIRCLE,
            quality=self._determine_quality(excitement),
            title=f"FINAL CIRCLE! {winner} wins it all!",
            description=f"The final circle decides everything - {winner} emerges victorious!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 15000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 8000,
            duration_seconds=25,
            primary_team=winner,
            excitement_score=excitement,
            viral_potential=min(85, excitement + 10),
            replay_value=90,
            suggested_angles=["overhead", "winner_pov", "final_moments"],
            needs_slow_motion=True,
            hashtags=["#FinalCircle", "#Winner", "#BGMI", "#ChickenDinner"],
        )
    
    def _create_funny_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create funny moment highlight"""
        
        return HighlightClip(
            clip_id=f"clip_funny_{int(time.time())}",
            category=HighlightCategory.FUNNY_MOMENT,
            quality=ClipQuality.GOOD,
            title=data.get("title", "Funny Moment! 😂"),
            description=data.get("description", "You have to see this!"),
            start_timestamp_ms=data.get("timestamp_ms", 0) - 5000,
            end_timestamp_ms=data.get("timestamp_ms", 0) + 5000,
            duration_seconds=10,
            primary_player=data.get("player_name", ""),
            excitement_score=35,
            viral_potential=85,
            hashtags=["#Funny", "#BGMI", "#Gaming", "#Fails"],
            platform_suitability={"tiktok": 95, "instagram_reels": 90},
        )
    
    def _create_comeback_clip(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HighlightClip:
        """Create comeback highlight"""
        
        team_name = data.get("team_name", "Unknown")
        from_position = data.get("from_position", 8)
        
        excitement = 60 + from_position * 3
        
        return HighlightClip(
            clip_id=f"clip_comeback_{int(time.time())}",
            category=HighlightCategory.COMEBACK,
            quality=self._determine_quality(excitement),
            title=f"THE COMEBACK! {team_name} rises from #{from_position}!",
            description=f"An incredible comeback story - {team_name} defies all odds!",
            start_timestamp_ms=data.get("start_time_ms", 0) - 10000,
            end_timestamp_ms=data.get("end_time_ms", 0) + 5000,
            duration_seconds=20,
            primary_team=team_name,
            excitement_score=excitement,
            viral_potential=min(90, excitement + 10),
            replay_value=85,
            hashtags=["#Comeback", "#NeverGiveUp", "#BGMI"],
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CLIP PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _process_clip(self, clip: HighlightClip) -> None:
        """Process a detected clip"""
        
        # Calculate platform suitability
        clip.platform_suitability = self._calculate_platform_suitability(clip)
        
        # Generate thumbnail timestamp
        clip.thumbnail_timestamp_ms = clip.start_timestamp_ms + (clip.duration_seconds * 0.5 * 1000)
        
        # Store clip
        self.clips[clip.clip_id] = clip
        
        self._log.info(
            f"Clip detected: {clip.title}",
            quality=clip.quality.value,
            excitement=round(clip.excitement_score, 1),
            viral=round(clip.viral_potential, 1),
        )
    
    def _calculate_platform_suitability(self, clip: HighlightClip) -> Dict[str, float]:
        """Calculate how suitable clip is for each platform"""
        
        suitability = {}
        
        for platform, factors in self.platform_factors.items():
            score = 50.0  # Base
            
            # Duration check
            if clip.duration_seconds <= factors["max_duration"]:
                score += 20
            
            # Category preference
            if clip.category.value in factors["preferred_categories"]:
                score += 20
            
            # Excitement bonus
            score += clip.excitement_score * 0.1
            
            # Clutch/comeback are universally good
            if clip.category in [HighlightCategory.CLUTCH_WIN, HighlightCategory.COMEBACK]:
                score += 15
            
            suitability[platform] = min(100, score)
        
        return suitability
    
    def _determine_quality(self, excitement_score: float) -> ClipQuality:
        """Determine clip quality from excitement score"""
        
        for quality, threshold in self.quality_thresholds.items():
            if excitement_score >= threshold:
                return quality
        
        return ClipQuality.AVERAGE
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_top_clips(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top clips by excitement score"""
        
        sorted_clips = sorted(
            self.clips.values(),
            key=lambda c: c.excitement_score,
            reverse=True,
        )[:limit]
        
        return [
            {
                "clip_id": c.clip_id,
                "title": c.title,
                "category": c.category.value,
                "quality": c.quality.value,
                "excitement": round(c.excitement_score, 1),
                "viral_potential": round(c.viral_potential, 1),
                "duration": round(c.duration_seconds, 1),
                "primary_player": c.primary_player,
                "primary_team": c.primary_team,
                "best_platform": max(c.platform_suitability, key=c.platform_suitability.get) if c.platform_suitability else "youtube_shorts",
            }
            for c in sorted_clips
        ]
    
    def get_clips_by_category(self, category: HighlightCategory) -> List[Dict[str, Any]]:
        """Get clips of specific category"""
        return [
            {
                "title": c.title,
                "quality": c.quality.value,
                "excitement": round(c.excitement_score, 1),
            }
            for c in self.clips.values()
            if c.category == category
        ]
    
    def get_clip(self, clip_id: str) -> Optional[Dict[str, Any]]:
        """Get specific clip details"""
        clip = self.clips.get(clip_id)
        if not clip:
            return None
        
        return {
            "clip_id": clip.clip_id,
            "title": clip.title,
            "description": clip.description,
            "category": clip.category.value,
            "quality": clip.quality.value,
            "duration": round(clip.duration_seconds, 1),
            "excitement": round(clip.excitement_score, 1),
            "viral_potential": round(clip.viral_potential, 1),
            "replay_value": round(clip.replay_value, 1),
            "primary_player": clip.primary_player,
            "primary_team": clip.primary_team,
            "suggested_angles": clip.suggested_angles,
            "needs_slow_motion": clip.needs_slow_motion,
            "platform_suitability": clip.platform_suitability,
            "hashtags": clip.hashtags,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get clip detector statistics"""
        return {
            "total_clips": len(self.clips),
            "by_quality": {
                q.value: len([c for c in self.clips.values() if c.quality == q])
                for q in ClipQuality
            },
            "by_category": {
                cat.value: len([c for c in self.clips.values() if c.category == cat])
                for cat in HighlightCategory
            },
            "top_clips": self.get_top_clips(5),
        }
    
    def reset(self) -> None:
        """Reset clip detector"""
        self.clips.clear()
        self.clip_history.clear()
        self.recent_kills.clear()
        self.recent_damage.clear()
        self._log.info("Clip detector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("highlight_moment")
        self.subscribe_to_event("fight_detected")
        # Chain: script generator -> clip detector -> highlight pipeline
        self.subscribe_to_event("commentary.line")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        commentary.line events run through the real clip pipeline
        (detect_clip) and publish highlight.clip_ready when a clip
        candidate is produced. All paths are exception-guarded.
        """
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", {}) if event else {}
            if not isinstance(data, dict):
                return

            if data.get("match_id"):
                self.config.match_id = data["match_id"]

            if event_type != "commentary.line":
                return

            clip_event_type = str(data.get("clip_event_type") or "multi_kill")
            match_id = data.get("match_id") or self.config.match_id

            event_data = {
                "player_name": "Unknown",
                "team_name": "",
                "kill_count": 3,
                "time_span": 10,
                "timestamp_ms": int(time.time() * 1000),
                "commentary_text": str(data.get("text", ""))[:300],
                "trigger": data.get("trigger", ""),
                "hype_level": data.get("hype_level", ""),
            }
            match_context = {"match_id": match_id}
            fight_context = {"fight_id": data.get("fight_id", "")}

            clip = self.detect_clip(
                clip_event_type, event_data, match_context, fight_context
            )
            if clip is None:
                return

            try:
                await self.event_bus.publish(
                    event_type="highlight.clip_ready",
                    data={
                        "clip_id": clip.clip_id,
                        "category": clip.category.value,
                        "quality": clip.quality.value,
                        "title": clip.title,
                        "excitement_score": round(clip.excitement_score, 1),
                        "fight_id": data.get("fight_id"),
                        "match_id": match_id,
                    },
                    priority=EventPriority.LOW,
                    source=self.config.agent_id,
                    match_id=match_id,
                )
            except Exception as e:
                self._log.warning(f"Failed to publish highlight.clip_ready: {e}")
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
