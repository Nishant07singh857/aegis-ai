# backend/app/agents/narrative_agents/storyline_builder.py
"""
AEGIS AI - Storyline Builder
Creates narrative arcs from match events, builds compelling
esports storylines for broadcast and content creation.
"""

import time
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.storyline")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StoryArc(str, Enum):
    """Classic narrative arcs"""
    UNDERDOG_RISING = "underdog_rising"       # Weak team surpasses expectations
    CHAMPION_FALL = "champion_fall"           # Dominant team struggles
    REVENGE_ARC = "revenge_arc"              # Team gets payback
    REDEMPTION = "redemption"                # Player/team redeems themselves
    RIVALRY_CLASH = "rivalry_clash"          # Historic rivals meet
    COMEBACK_KID = "comeback_kid"            # Incredible comeback
    LAST_DANCE = "last_dance"               # Final tournament for veteran
    NEW_GUARD = "new_guard"                 # New team challenges established order
    DOMINANCE = "dominance"                 # One team dominating
    CINDERELLA = "cinderella"               # Unknown team makes deep run

class StorylineStatus(str, Enum):
    """Storyline lifecycle status"""
    EMERGING = "emerging"       # Just detected
    DEVELOPING = "developing"   # Growing evidence
    ACTIVE = "active"          # Currently happening
    CLIMAX = "climax"          # Peak moment
    RESOLVED = "resolved"      # Story concluded
    ARCHIVED = "archived"      # Historical record

class StoryMoment(str, Enum):
    """Key moments that advance storylines"""
    UPSET_WIN = "upset_win"
    CLUTCH_PLAY = "clutch_play"
    TEAM_WIPE = "team_wipe"
    COMEBACK_START = "comeback_start"
    RECORD_BREAK = "record_break"
    ELIMINATION = "elimination"
    CHAMPIONSHIP_WIN = "championship_win"
    RIVALRY_ENCOUNTER = "rivalry_encounter"

@dataclass
class StoryBeat:
    """A single beat/moment in a storyline"""
    beat_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    moment_type: StoryMoment = StoryMoment.CLUTCH_PLAY
    timestamp: float = field(default_factory=time.time)
    description: str = ""
    teams_involved: List[str] = field(default_factory=list)
    players_involved: List[str] = field(default_factory=list)
    importance: float = 0.5  # 0-1
    emotional_weight: float = 0.5  # 0-1
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class Storyline:
    """A complete narrative storyline"""
    storyline_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    title: str = ""
    arc_type: StoryArc = StoryArc.UNDERDOG_RISING
    status: StorylineStatus = StorylineStatus.EMERGING
    
    # Description
    summary: str = ""
    full_narrative: str = ""
    
    # Participants
    primary_team: str = ""
    primary_player: str = ""
    secondary_teams: List[str] = field(default_factory=list)
    secondary_players: List[str] = field(default_factory=list)
    
    # Story beats
    beats: List[StoryBeat] = field(default_factory=list)
    key_moments: List[StoryBeat] = field(default_factory=list)
    
    # Metrics
    importance_score: float = 0.0  # 0-100
    entertainment_value: float = 0.0  # 0-100
    emotional_impact: float = 0.0  # 0-100
    
    # Timing
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    
    # Broadcast
    broadcast_priority: int = 0  # Higher = more important to mention
    mention_count: int = 0
    last_mentioned: float = 0.0
    
    # Tags
    tags: List[str] = field(default_factory=list)
    tournament_id: str = ""
    match_id: str = ""


class StorylineBuilder(BaseAgent):
    """
    Builds narrative storylines from match events.
    
    Creates compelling esports narratives:
    - Underdog stories
    - Rivalry developments
    - Comeback arcs
    - Redemption stories
    - Championship runs
    - Player spotlights
    
    These storylines feed the commentary system
    and provide context for broadcast production.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active storylines
        self.active_storylines: Dict[str, Storyline] = {}
        
        # Storyline history
        self.storyline_history: deque = deque(maxlen=100)
        
        # Team narratives
        self.team_narratives: Dict[str, List[str]] = defaultdict(list)
        
        # Story arc templates
        self.arc_templates = {
            StoryArc.UNDERDOG_RISING: {
                "title_template": "{team}: The Underdog Rise",
                "importance_base": 70,
                "required_beats": [StoryMoment.UPSET_WIN, StoryMoment.CLUTCH_PLAY],
            },
            StoryArc.REVENGE_ARC: {
                "title_template": "{team} Seeks Revenge Against {opponent}",
                "importance_base": 75,
                "required_beats": [StoryMoment.RIVALRY_ENCOUNTER],
            },
            StoryArc.COMEBACK_KID: {
                "title_template": "{player}: The Comeback Story",
                "importance_base": 80,
                "required_beats": [StoryMoment.COMEBACK_START],
            },
            StoryArc.DOMINANCE: {
                "title_template": "{team}: A Legacy of Dominance",
                "importance_base": 65,
                "required_beats": [StoryMoment.CHAMPIONSHIP_WIN],
            },
            StoryArc.CINDERELLA: {
                "title_template": "{team}: The Cinderella Run",
                "importance_base": 85,
                "required_beats": [StoryMoment.UPSET_WIN, StoryMoment.UPSET_WIN],
            },
        }
        
        self._log = logger.bind(component="storyline_builder")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STORYLINE CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def create_storyline(
        self,
        arc_type: StoryArc,
        primary_team: str,
        primary_player: str = "",
        secondary_teams: List[str] = None,
        tournament_id: str = "",
        match_id: str = "",
    ) -> Storyline:
        """Create a new storyline"""
        
        template = self.arc_templates.get(arc_type, {})
        
        title = template.get("title_template", "{team} Story").format(
            team=primary_team,
            player=primary_player,
            opponent=secondary_teams[0] if secondary_teams else "Rivals",
        )
        
        storyline = Storyline(
            title=title,
            arc_type=arc_type,
            primary_team=primary_team,
            primary_player=primary_player,
            secondary_teams=secondary_teams or [],
            importance_score=template.get("importance_base", 50),
            tournament_id=tournament_id,
            match_id=match_id,
        )
        
        self.active_storylines[storyline.storyline_id] = storyline
        self.team_narratives[primary_team].append(storyline.storyline_id)
        
        self._log.info(
            f"Storyline created: {title}",
            arc=arc_type.value,
        )
        
        return storyline
    
    def add_story_beat(
        self,
        storyline_id: str,
        moment_type: StoryMoment,
        description: str,
        teams: List[str],
        players: List[str] = None,
        importance: float = 0.5,
        emotional_weight: float = 0.5,
    ) -> Optional[StoryBeat]:
        """Add a beat to an existing storyline"""
        
        storyline = self.active_storylines.get(storyline_id)
        if not storyline:
            return None
        
        beat = StoryBeat(
            moment_type=moment_type,
            description=description,
            teams_involved=teams,
            players_involved=players or [],
            importance=importance,
            emotional_weight=emotional_weight,
        )
        
        storyline.beats.append(beat)
        
        # Check if this is a key moment
        if importance > 0.7:
            storyline.key_moments.append(beat)
            storyline.status = StorylineStatus.CLIMAX if importance > 0.9 else StorylineStatus.ACTIVE
        
        # Update importance
        storyline.importance_score = min(100, storyline.importance_score + importance * 10)
        storyline.entertainment_value = min(100, storyline.entertainment_value + emotional_weight * 15)
        storyline.last_updated = time.time()
        
        # Check for required beats to advance arc
        self._check_arc_progression(storyline)
        
        # Generate narrative
        self._update_narrative(storyline)
        
        return beat
    
    def detect_storyline(
        self,
        events: List[Dict[str, Any]],
        match_context: Dict[str, Any],
    ) -> List[Storyline]:
        """Auto-detect potential storylines from events"""
        
        detected = []
        
        # Check for underdog story
        if self._detect_underdog(events, match_context):
            storyline = self.create_storyline(
                arc_type=StoryArc.UNDERDOG_RISING,
                primary_team=match_context.get("underdog_team", ""),
                tournament_id=match_context.get("tournament_id", ""),
                match_id=match_context.get("match_id", ""),
            )
            detected.append(storyline)
        
        # Check for comeback story
        if self._detect_comeback(events):
            storyline = self.create_storyline(
                arc_type=StoryArc.COMEBACK_KID,
                primary_team=events[0].get("team_name", ""),
                primary_player=events[0].get("player_name", ""),
            )
            detected.append(storyline)
        
        # Check for dominance
        if self._detect_dominance(events, match_context):
            storyline = self.create_storyline(
                arc_type=StoryArc.DOMINANCE,
                primary_team=match_context.get("dominant_team", ""),
            )
            detected.append(storyline)
        
        # Check for Cinderella run
        if self._detect_cinderella(events, match_context):
            storyline = self.create_storyline(
                arc_type=StoryArc.CINDERELLA,
                primary_team=match_context.get("cinderella_team", ""),
            )
            detected.append(storyline)
        
        return detected
    
    def _detect_underdog(
        self,
        events: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> bool:
        """Detect underdog storyline"""
        upsets = [e for e in events if e.get("type") == "upset_win"]
        return len(upsets) >= 2
    
    def _detect_comeback(self, events: List[Dict[str, Any]]) -> bool:
        """Detect comeback storyline"""
        comebacks = [e for e in events if e.get("type") == "comeback"]
        return len(comebacks) >= 1
    
    def _detect_dominance(
        self,
        events: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> bool:
        """Detect dominance storyline"""
        wins = [e for e in events if e.get("type") == "match_win"]
        return len(wins) >= 3
    
    def _detect_cinderella(
        self,
        events: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> bool:
        """Detect Cinderella storyline"""
        team_rank = context.get("team_seed", 16)
        wins = [e for e in events if e.get("type") == "match_win"]
        return team_rank >= 12 and len(wins) >= 2
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # NARRATIVE GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_narrative(self, storyline: Storyline) -> None:
        """Update the full narrative text"""
        
        parts = []
        
        # Introduction based on arc type
        intros = {
            StoryArc.UNDERDOG_RISING: f"{storyline.primary_team} came into this tournament with low expectations, but they've been proving everyone wrong.",
            StoryArc.REVENGE_ARC: f"The history between {storyline.primary_team} and {storyline.secondary_teams[0] if storyline.secondary_teams else 'their rivals'} runs deep.",
            StoryArc.COMEBACK_KID: f"Against all odds, {storyline.primary_player or storyline.primary_team} refused to give up.",
            StoryArc.DOMINANCE: f"{storyline.primary_team} has been establishing themselves as the team to beat.",
            StoryArc.CINDERELLA: f"Nobody expected {storyline.primary_team} to make it this far. This is the stuff of fairytales.",
        }
        
        parts.append(intros.get(storyline.arc_type, f"The story of {storyline.primary_team} continues to unfold."))
        
        # Add key moments
        for beat in storyline.key_moments[-3:]:
            parts.append(beat.description)
        
        # Closing based on status
        if storyline.status == StorylineStatus.CLIMAX:
            parts.append("This is a moment that will be remembered.")
        elif storyline.status == StorylineStatus.RESOLVED:
            parts.append("And so, another chapter in esports history is written.")
        
        storyline.full_narrative = " ".join(parts)
        storyline.summary = parts[0] if parts else ""
    
    def _check_arc_progression(self, storyline: Storyline) -> None:
        """Check if storyline arc has progressed"""
        
        template = self.arc_templates.get(storyline.arc_type, {})
        required = template.get("required_beats", [])
        
        beat_types = [b.moment_type for b in storyline.beats]
        
        all_required = all(r in beat_types for r in required)
        
        if all_required and storyline.status == StorylineStatus.EMERGING:
            storyline.status = StorylineStatus.DEVELOPING
            self._log.info(f"Storyline progressing: {storyline.title}")
        
        if len(storyline.key_moments) >= 3:
            storyline.status = StorylineStatus.ACTIVE
    
    def resolve_storyline(self, storyline_id: str, resolution: str) -> None:
        """Resolve a storyline"""
        
        storyline = self.active_storylines.get(storyline_id)
        if not storyline:
            return
        
        storyline.status = StorylineStatus.RESOLVED
        storyline.resolved_at = time.time()
        
        # Add final beat
        self.add_story_beat(
            storyline_id,
            StoryMoment.CHAMPIONSHIP_WIN,
            resolution,
            [storyline.primary_team],
            importance=1.0,
            emotional_weight=1.0,
        )
        
        # Archive
        self.storyline_history.append(storyline)
        self.active_storylines.pop(storyline_id, None)
        
        self._log.info(f"Storyline resolved: {storyline.title}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_storylines(
        self,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Get active storylines sorted by importance"""
        
        sorted_stories = sorted(
            self.active_storylines.values(),
            key=lambda s: s.importance_score,
            reverse=True,
        )[:limit]
        
        return [
            {
                "id": s.storyline_id,
                "title": s.title,
                "arc": s.arc_type.value,
                "status": s.status.value,
                "summary": s.summary,
                "importance": round(s.importance_score, 1),
                "entertainment": round(s.entertainment_value, 1),
                "key_beats": len(s.key_moments),
                "broadcast_priority": s.broadcast_priority,
            }
            for s in sorted_stories
        ]
    
    def get_team_storylines(self, team_id: str) -> List[Dict[str, Any]]:
        """Get storylines for a specific team"""
        
        storyline_ids = self.team_narratives.get(team_id, [])
        storylines = []
        
        for sid in storyline_ids:
            s = self.active_storylines.get(sid)
            if s:
                storylines.append({
                    "title": s.title,
                    "arc": s.arc_type.value,
                    "status": s.status.value,
                    "importance": round(s.importance_score, 1),
                })
        
        return storylines
    
    def get_broadcast_storylines(self, limit: int = 3) -> List[Dict[str, Any]]:
        """Get top storylines for broadcast mention"""
        
        active = [
            s for s in self.active_storylines.values()
            if s.status in [StorylineStatus.ACTIVE, StorylineStatus.CLIMAX]
        ]
        
        sorted_stories = sorted(
            active,
            key=lambda s: (s.broadcast_priority, s.importance_score),
            reverse=True,
        )[:limit]
        
        return [
            {
                "title": s.title,
                "summary": s.summary,
                "arc": s.arc_type.value,
            }
            for s in sorted_stories
        ]
    
    def get_stats(self) -> Dict[str, Any]:
        """Get storyline builder statistics"""
        return {
            "active_storylines": len(self.active_storylines),
            "total_created": len(self.storyline_history) + len(self.active_storylines),
            "by_arc": {
                arc.value: len([s for s in self.active_storylines.values() if s.arc_type == arc])
                for arc in StoryArc
            },
            "top_stories": self.get_active_storylines(5),
        }
    
    def reset(self) -> None:
        """Reset storyline builder"""
        self.active_storylines.clear()
        self.storyline_history.clear()
        self.team_narratives.clear()
        self._log.info("Storyline builder reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("fight_detected")
        self.subscribe_to_event("match_state_change")

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
