# backend/app/agents/commentary_agents/hype_detector.py
"""
AEGIS AI - Hype Detector
Detects hype-worthy moments in real-time for commentary emphasis.
Identifies moments that deserve maximum energy from casters.
"""

import time
import math
import uuid
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventPriority, EventRecord

logger = structlog.get_logger("aegis.agents.hype")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class HypeLevel(str, Enum):
    """Hype intensity levels"""
    MILD = "mild"           # Slightly interesting
    NOTABLE = "notable"     # Worth mentioning
    EXCITING = "exciting"   # Get the crowd going
    HYPE = "hype"          # Maximum energy
    LEGENDARY = "legendary" # All-time great moment
    HISTORIC = "historic"   # Once in a lifetime

class HypeCategory(str, Enum):
    """Categories of hype moments"""
    CLUTCH = "clutch"
    TEAM_WIPE = "team_wipe"
    MULTI_KILL = "multi_kill"
    LONG_RANGE = "long_range"
    GRENADE = "grenade"
    HEADSHOT = "headshot"
    COMEBACK = "comeback"
    UPSET = "upset"
    RECORD_BREAK = "record_break"
    FINAL_MOMENT = "final_moment"
    RIVALRY = "rivalry"
    FIRST_BLOOD = "first_blood"

@dataclass
class HypeMoment:
    """A detected hype-worthy moment"""
    moment_id: str
    category: HypeCategory
    level: HypeLevel
    hype_score: float = 0.0  # 0-100
    
    # What happened
    description: str = ""
    player_name: str = ""
    team_name: str = ""
    opponent_name: str = ""
    
    # Context
    match_context: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    fight_id: Optional[str] = None
    
    # Broadcast value
    should_highlight: bool = False
    replay_priority: int = 0  # 0-100
    entertainment_value: float = 0.0
    
    # Metadata
    related_events: List[str] = field(default_factory=list)
    commentator_notes: str = ""

@dataclass
class HypeHistory:
    """History of hype moments in current match"""
    moments: List[HypeMoment] = field(default_factory=list)
    peak_moment: Optional[HypeMoment] = None
    hype_count: int = 0
    legendary_count: int = 0
    last_hype_time: float = 0.0
    hype_cooldown: float = 5.0  # Seconds between hype moments


class HypeDetector(BaseAgent):
    """
    Detects hype-worthy moments for commentary emphasis.
    
    Hype Detection Signals:
    1. Clutch situations (1vX wins especially)
    2. Multi-kills (2+ kills in quick succession)
    3. Team wipes (squad eliminated)
    4. Long-range kills (200m+)
    5. Grenade kills (especially multi-kill grenades)
    6. Headshot chains
    7. Comeback victories
    8. Upset victories (underdog winning)
    9. Record-breaking performances
    10. Final circle decisive moments
    11. Rivalry confrontations
    
    Each moment is scored and categorized for the commentary
    system to react with appropriate energy levels.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.history = HypeHistory()
        
        # Hype scoring weights
        self.weights = {
            HypeCategory.CLUTCH: 0.30,
            HypeCategory.TEAM_WIPE: 0.20,
            HypeCategory.MULTI_KILL: 0.15,
            HypeCategory.FINAL_MOMENT: 0.12,
            HypeCategory.RECORD_BREAK: 0.10,
            HypeCategory.GRENADE: 0.05,
            HypeCategory.LONG_RANGE: 0.03,
            HypeCategory.HEADSHOT: 0.02,
            HypeCategory.COMEBACK: 0.02,
            HypeCategory.UPSET: 0.01,
            HypeCategory.RIVALRY: 0.05,
            HypeCategory.FIRST_BLOOD: 0.01,
        }
        
        # Base hype scores per category
        self.base_scores = {
            HypeCategory.CLUTCH: 80,
            HypeCategory.TEAM_WIPE: 65,
            HypeCategory.MULTI_KILL: 55,
            HypeCategory.FINAL_MOMENT: 70,
            HypeCategory.RECORD_BREAK: 85,
            HypeCategory.GRENADE: 40,
            HypeCategory.LONG_RANGE: 30,
            HypeCategory.HEADSHOT: 20,
            HypeCategory.COMEBACK: 60,
            HypeCategory.UPSET: 50,
            HypeCategory.RIVALRY: 45,
            HypeCategory.FIRST_BLOOD: 25,
        }
        
        # Kill tracking for multi-kill detection
        self.recent_kills: deque = deque(maxlen=20)
        self.player_kill_streaks: Dict[str, List[float]] = {}
        
        # Damage tracking
        self.recent_damage: deque = deque(maxlen=100)
        
        # Configuration
        self.multi_kill_window = 8.0  # Seconds for multi-kill
        self.team_wipe_window = 15.0  # Seconds for team wipe
        self.hype_decay_rate = 3.0    # Points per second
        
        self._log = logger.bind(component="hype_detector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HYPE DETECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def detect_hype(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        match_context: Dict[str, Any],
    ) -> Optional[HypeMoment]:
        """
        Detect if an event is hype-worthy.
        
        Args:
            event_type: Type of event
            event_data: Event details
            match_context: Current match state
            
        Returns:
            HypeMoment if hype detected, None otherwise
        """
        
        # Check cooldown
        if time.time() - self.history.last_hype_time < self.history.hype_cooldown:
            # Only allow legendary moments during cooldown
            pass
        
        moment = None
        
        if event_type == "kill":
            moment = self._check_kill_hype(event_data, match_context)
        elif event_type == "multi_kill":
            moment = self._check_multi_kill_hype(event_data, match_context)
        elif event_type == "team_wipe":
            moment = self._check_team_wipe_hype(event_data, match_context)
        elif event_type == "clutch_start":
            moment = self._check_clutch_hype(event_data, match_context)
        elif event_type == "clutch_win":
            moment = self._check_clutch_win_hype(event_data, match_context)
        elif event_type == "grenade_kill":
            moment = self._check_grenade_hype(event_data, match_context)
        elif event_type == "long_range_kill":
            moment = self._check_long_range_hype(event_data, match_context)
        elif event_type == "comeback":
            moment = self._check_comeback_hype(event_data, match_context)
        elif event_type == "final_moment":
            moment = self._check_final_moment_hype(event_data, match_context)
        
        if moment:
            self._process_hype_moment(moment)
        
        return moment
    
    def _check_kill_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[HypeMoment]:
        """Check if a kill is hype-worthy"""
        
        player_name = data.get("player_name", "Unknown")
        weapon = data.get("weapon", "")
        distance = data.get("distance", 0)
        headshot = data.get("headshot", False)
        
        # Track for multi-kill
        self.recent_kills.append({
            "player": player_name,
            "team": data.get("team_name", ""),
            "timestamp": time.time(),
            "weapon": weapon,
        })
        
        # Track player streak
        if player_name not in self.player_kill_streaks:
            self.player_kill_streaks[player_name] = []
        self.player_kill_streaks[player_name].append(time.time())
        
        # Clean old kills
        self.player_kill_streaks[player_name] = [
            t for t in self.player_kill_streaks[player_name]
            if time.time() - t < self.multi_kill_window
        ]
        
        streak = len(self.player_kill_streaks[player_name])
        
        # Check for multi-kill
        if streak >= 3:
            return HypeMoment(
                moment_id=f"multi_kill_{int(time.time())}",
                category=HypeCategory.MULTI_KILL,
                level=HypeLevel.HYPE if streak >= 4 else HypeLevel.EXCITING,
                hype_score=min(90, 60 + streak * 8),
                description=f"{player_name} with a {streak}-kill streak!",
                player_name=player_name,
                team_name=data.get("team_name", ""),
            )
        
        # Headshot hype
        if headshot and distance > 100:
            return HypeMoment(
                moment_id=f"headshot_{int(time.time())}",
                category=HypeCategory.HEADSHOT,
                level=HypeLevel.NOTABLE,
                hype_score=min(50, 25 + distance * 0.1),
                description=f"What a headshot from {distance}m!",
                player_name=player_name,
                team_name=data.get("team_name", ""),
            )
        
        return None
    
    def _check_multi_kill_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[HypeMoment]:
        """Check multi-kill hype level"""
        
        kill_count = data.get("kill_count", 2)
        time_span = data.get("time_span", 10)
        player_name = data.get("player_name", "Unknown")
        
        # Score based on kills and speed
        kills_per_second = kill_count / max(1, time_span)
        
        if kill_count >= 5:
            level = HypeLevel.LEGENDARY
            score = 95
        elif kill_count >= 4:
            level = HypeLevel.HYPE
            score = 85
        elif kill_count >= 3:
            level = HypeLevel.EXCITING
            score = 70
        else:
            level = HypeLevel.NOTABLE
            score = 50
        
        # Bonus for speed
        if kills_per_second > 0.5:
            score += 10
            if level == HypeLevel.EXCITING:
                level = HypeLevel.HYPE
        
        return HypeMoment(
            moment_id=f"multi_{int(time.time())}",
            category=HypeCategory.MULTI_KILL,
            level=level,
            hype_score=min(100, score),
            description=f"{kill_count} kills in {time_span:.1f}s by {player_name}!",
            player_name=player_name,
            team_name=data.get("team_name", ""),
        )
    
    def _check_team_wipe_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HypeMoment:
        """Check team wipe hype"""
        
        team_name = data.get("team_name", "Unknown")
        eliminator = data.get("eliminator", "Unknown")
        placement = data.get("placement", 16)
        
        # Later placements = bigger impact
        impact = (17 - placement) / 16  # 1st place = max impact
        
        if placement <= 3:
            level = HypeLevel.HYPE
            score = 80 + impact * 15
        elif placement <= 5:
            level = HypeLevel.EXCITING
            score = 65 + impact * 10
        else:
            level = HypeLevel.NOTABLE
            score = 40 + impact * 15
        
        # Check if it's a rivalry elimination
        if context.get("is_rivalry", False):
            score += 15
            if level == HypeLevel.EXCITING:
                level = HypeLevel.HYPE
        
        return HypeMoment(
            moment_id=f"wipe_{int(time.time())}",
            category=HypeCategory.TEAM_WIPE,
            level=level,
            hype_score=min(100, score),
            description=f"{team_name} eliminated by {eliminator}! Place #{placement}",
            team_name=team_name,
            opponent_name=eliminator,
        )
    
    def _check_clutch_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HypeMoment:
        """Check clutch situation hype"""
        
        player_name = data.get("player_name", "Unknown")
        enemy_count = data.get("enemy_count", 4)
        probability = data.get("probability", 0.1)
        
        # More enemies = more hype
        if enemy_count >= 4:
            level = HypeLevel.HYPE
            score = 85
        elif enemy_count == 3:
            level = HypeLevel.EXCITING
            score = 75
        else:
            level = HypeLevel.NOTABLE
            score = 55
        
        # Lower probability = more hype
        score += (1 - probability) * 15
        
        return HypeMoment(
            moment_id=f"clutch_{int(time.time())}",
            category=HypeCategory.CLUTCH,
            level=level,
            hype_score=min(100, score),
            description=f"CLUTCH! {player_name} 1v{enemy_count}!",
            player_name=player_name,
            team_name=data.get("team_name", ""),
        )
    
    def _check_clutch_win_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HypeMoment:
        """Check clutch win hype - these are LEGENDARY"""
        
        player_name = data.get("player_name", "Unknown")
        enemies_killed = data.get("enemies_killed", 0)
        probability = data.get("probability", 0.1)
        
        # Clutch wins are always high hype
        if probability < 0.1:  # Less than 10% chance
            level = HypeLevel.LEGENDARY
            score = 98
        elif probability < 0.2:
            level = HypeLevel.HYPE
            score = 90
        else:
            level = HypeLevel.EXCITING
            score = 80
        
        if enemies_killed >= 4:
            level = HypeLevel.LEGENDARY
            score = 100
        
        return HypeMoment(
            moment_id=f"clutch_win_{int(time.time())}",
            category=HypeCategory.CLUTCH,
            level=level,
            hype_score=min(100, score),
            description=f"CLUTCH COMPLETE! {player_name} wins 1v{enemies_killed}!",
            player_name=player_name,
            team_name=data.get("team_name", ""),
            should_highlight=True,
            replay_priority=100,
            entertainment_value=100,
        )
    
    def _check_grenade_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[HypeMoment]:
        """Check grenade kill hype"""
        
        player_name = data.get("player_name", "Unknown")
        victims = data.get("victims", 1)
        
        if victims >= 3:
            level = HypeLevel.HYPE
            score = 85
        elif victims >= 2:
            level = HypeLevel.EXCITING
            score = 65
        else:
            level = HypeLevel.NOTABLE
            score = 40
        
        return HypeMoment(
            moment_id=f"nade_{int(time.time())}",
            category=HypeCategory.GRENADE,
            level=level,
            hype_score=min(100, score),
            description=f"WHAT A GRENADE! {victims} kills by {player_name}!",
            player_name=player_name,
            team_name=data.get("team_name", ""),
        )
    
    def _check_long_range_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[HypeMoment]:
        """Check long-range kill hype"""
        
        distance = data.get("distance", 0)
        
        if distance >= 300:
            level = HypeLevel.HYPE
            score = 80
        elif distance >= 200:
            level = HypeLevel.EXCITING
            score = 60
        elif distance >= 150:
            level = HypeLevel.NOTABLE
            score = 40
        else:
            return None
        
        return HypeMoment(
            moment_id=f"range_{int(time.time())}",
            category=HypeCategory.LONG_RANGE,
            level=level,
            hype_score=min(100, score),
            description=f"Incredible {distance}m kill!",
            player_name=data.get("player_name", ""),
            team_name=data.get("team_name", ""),
        )
    
    def _check_comeback_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HypeMoment:
        """Check comeback hype"""
        
        team_name = data.get("team_name", "Unknown")
        from_position = data.get("from_position", 10)
        
        score = 50 + (from_position - 3) * 5
        
        if from_position >= 8:
            level = HypeLevel.HYPE
        elif from_position >= 5:
            level = HypeLevel.EXCITING
        else:
            level = HypeLevel.NOTABLE
        
        return HypeMoment(
            moment_id=f"comeback_{int(time.time())}",
            category=HypeCategory.COMEBACK,
            level=level,
            hype_score=min(100, score),
            description=f"What a comeback by {team_name}!",
            team_name=team_name,
        )
    
    def _check_final_moment_hype(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> HypeMoment:
        """Check final moment hype"""
        
        description = data.get("description", "Final moment!")
        teams_alive = context.get("teams_alive", 2)
        
        if teams_alive <= 2:
            level = HypeLevel.LEGENDARY
            score = 95
        else:
            level = HypeLevel.HYPE
            score = 80
        
        return HypeMoment(
            moment_id=f"final_{int(time.time())}",
            category=HypeCategory.FINAL_MOMENT,
            level=level,
            hype_score=min(100, score),
            description=description,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HYPE PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _process_hype_moment(self, moment: HypeMoment) -> None:
        """Process a detected hype moment"""
        
        self.history.moments.append(moment)
        self.history.hype_count += 1
        self.history.last_hype_time = time.time()
        
        if moment.level == HypeLevel.LEGENDARY:
            self.history.legendary_count += 1
        
        # Update peak moment
        if not self.history.peak_moment or moment.hype_score > self.history.peak_moment.hype_score:
            self.history.peak_moment = moment
        
        # Determine if should be highlighted for replay
        if moment.hype_score > 75:
            moment.should_highlight = True
            moment.replay_priority = int(moment.hype_score)
        
        self._log.info(
            f"🔥 HYPE DETECTED: {moment.level.value.upper()} | "
            f"Score: {moment.hype_score:.0f} | "
            f"{moment.description}"
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HYPE LEVEL CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_current_hype_level(self) -> Tuple[HypeLevel, float]:
        """Get current overall hype level and score"""
        
        if not self.history.moments:
            return HypeLevel.MILD, 10.0
        
        # Get recent moments
        recent = [
            m for m in self.history.moments
            if time.time() - m.timestamp < 30
        ]
        
        if not recent:
            return HypeLevel.MILD, 10.0
        
        # Calculate current hype score
        score = 0
        for moment in recent:
            age = time.time() - moment.timestamp
            decay = math.exp(-age / 15)  # Half-life of 15 seconds
            score = max(score, moment.hype_score * decay)
        
        # Determine level
        if score >= 90:
            level = HypeLevel.LEGENDARY
        elif score >= 75:
            level = HypeLevel.HYPE
        elif score >= 55:
            level = HypeLevel.EXCITING
        elif score >= 35:
            level = HypeLevel.NOTABLE
        else:
            level = HypeLevel.MILD
        
        return level, score
    
    def get_recommended_energy(self) -> float:
        """Get recommended energy level for commentary (0-100)"""
        _, score = self.get_current_hype_level()
        return score
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_recent_hype(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Get recent hype moments"""
        return [
            {
                "description": m.description,
                "level": m.level.value,
                "score": round(m.hype_score, 1),
                "player": m.player_name,
                "team": m.team_name,
                "seconds_ago": round(time.time() - m.timestamp, 1),
            }
            for m in self.history.moments[-limit:]
        ]
    
    def get_peak_moment(self) -> Optional[Dict[str, Any]]:
        """Get the peak hype moment of the match"""
        if not self.history.peak_moment:
            return None
        
        m = self.history.peak_moment
        return {
            "description": m.description,
            "level": m.level.value,
            "score": round(m.hype_score, 1),
            "player": m.player_name,
            "team": m.team_name,
            "category": m.category.value,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get hype detector statistics"""
        return {
            "total_hype_moments": self.history.hype_count,
            "legendary_moments": self.history.legendary_count,
            "current_level": self.get_current_hype_level()[0].value,
            "current_score": round(self.get_current_hype_level()[1], 1),
            "peak_moment": self.get_peak_moment(),
            "recent_hype": self.get_recent_hype(3),
        }
    
    def reset(self) -> None:
        """Reset hype detector"""
        self.history = HypeHistory()
        self.recent_kills.clear()
        self.player_kill_streaks.clear()
        self.recent_damage.clear()
        self._log.info("Hype detector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    # Fight type -> suggested clip category for the highlight chain.
    # Documented heuristic: the clip detector needs one of its known
    # event types; this maps the fight classifier's output to the closest
    # clip kind. "multi_kill" is the generic exciting-fight fallback.
    _FIGHT_CLIP_TYPE = {
        "third_party": "squad_wipe",
        "close_quarters": "grenade_kill",
        "utility_fight": "grenade_kill",
        "long_range": "long_range_kill",
        "ambush": "multi_kill",
        "clutch": "clutch_win",
        "push": "multi_kill",
        "hold_and_defend": "multi_kill",
        "retreat": "comeback_win",
        "mid_range": "multi_kill",
    }

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("highlight_moment")
        self.subscribe_to_event("fight_detected")
        # Chain: fight detector -> hype detector -> script generator
        self.subscribe_to_event("fight.detected")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    @staticmethod
    def _level_for_intensity(intensity: float) -> "HypeLevel":
        """Map a 0-100 fight intensity to a hype level."""
        if intensity >= 90:
            return HypeLevel.LEGENDARY
        if intensity >= 75:
            return HypeLevel.HYPE
        if intensity >= 55:
            return HypeLevel.EXCITING
        if intensity >= 35:
            return HypeLevel.NOTABLE
        return HypeLevel.MILD

    def _moment_from_fight(self, data: Dict[str, Any]) -> HypeMoment:
        """Build a real HypeMoment from a fight.detected payload."""
        teams = data.get("teams_involved", []) or []
        intensity = float(data.get("intensity", 50.0) or 50.0)
        fight_type = str(data.get("fight_type", "mid_range"))
        level = self._level_for_intensity(intensity)

        if len(teams) >= 3:
            category = HypeCategory.RIVALRY
        elif intensity >= 85:
            category = HypeCategory.MULTI_KILL
        else:
            category = HypeCategory.FIRST_BLOOD

        return HypeMoment(
            moment_id=f"hype_{uuid.uuid4().hex[:8]}",
            category=category,
            level=level,
            hype_score=round(intensity, 1),
            description=(
                f"Fight erupted between {', '.join(teams) or 'unknown teams'} "
                f"({fight_type}, intensity {intensity:.0f})"
            ),
            team_name=teams[0] if teams else "",
            opponent_name=teams[1] if len(teams) > 1 else "",
            match_context={"match_id": data.get("match_id", "")},
            fight_id=data.get("fight_id"),
        )

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        fight.detected events run through the real hype pipeline
        (_process_hype_moment) and publish hype.spike for the commentary
        chain. All paths are exception-guarded.
        """
        try:
            event = getattr(record, "event", None)
            event_type = getattr(event, "event_type", "") if event else ""
            data = getattr(event, "data", {}) if event else {}
            if not isinstance(data, dict):
                return

            if data.get("match_id"):
                self.config.match_id = data["match_id"]

            if event_type != "fight.detected":
                return

            moment = self._moment_from_fight(data)
            self._process_hype_moment(moment)

            clip_event_type = self._FIGHT_CLIP_TYPE.get(
                str(data.get("fight_type", "")), "multi_kill"
            )
            try:
                await self.event_bus.publish(
                    event_type="hype.spike",
                    data={
                        "hype_level": moment.level.value,
                        "hype_score": moment.hype_score,
                        "reason": moment.description,
                        "fight_id": moment.fight_id,
                        "clip_event_type": clip_event_type,
                        "fight_type": data.get("fight_type"),
                        "teams_involved": data.get("teams_involved", []),
                        "intensity": data.get("intensity"),
                        "match_id": data.get("match_id")
                        or self.config.match_id,
                    },
                    priority=EventPriority.HIGH,
                    source=self.config.agent_id,
                    match_id=data.get("match_id") or self.config.match_id,
                )
            except Exception as e:
                self._log.warning(f"Failed to publish hype.spike: {e}")
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
