# backend/app/ml/commentary/context_builder.py
"""
AEGIS AI - Commentary Context Builder
Builds rich context for commentary generation from match state,
player stats, team history, rivalries, and storylines.
"""

import time
from typing import Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.ml.context")


class ContextPriority(str, Enum):
    """Priority levels for context elements"""
    CRITICAL = "critical"     # Must include (current event)
    HIGH = "high"            # Very relevant (active fight, clutch)
    MEDIUM = "medium"        # Helpful context (player stats, zone)
    LOW = "low"             # Nice to have (historical, storylines)
    BACKGROUND = "background"  # Optional flavor


@dataclass
class CommentaryContext:
    """Complete context for commentary generation"""
    # Current event
    event_type: str = ""
    event_description: str = ""
    event_priority: ContextPriority = ContextPriority.CRITICAL
    
    # Primary participants
    primary_player: str = ""
    primary_player_id: str = ""
    primary_team: str = ""
    primary_team_id: str = ""
    
    # Secondary participants
    secondary_player: str = ""
    secondary_player_id: str = ""
    secondary_team: str = ""
    secondary_team_id: str = ""
    
    # Event details
    weapon: str = ""
    damage: int = 0
    headshot: bool = False
    distance: float = 0.0
    kill_count: int = 0
    time_span: float = 0.0  # For multi-kills
    
    # Match state
    zone_phase: int = 0
    teams_alive: int = 16
    players_alive: int = 64
    match_time: float = 0.0
    match_name: str = ""
    tournament_name: str = ""
    
    # Player context
    player_kills: int = 0
    player_damage: int = 0
    player_knocks: int = 0
    player_clutches: int = 0
    player_mvp_count: int = 0
    player_playstyle: str = ""
    player_signature_weapon: str = ""
    player_recent_form: str = ""
    
    # Team context
    team_kills: int = 0
    team_damage: int = 0
    team_placement: int = 0
    team_playstyle: str = ""
    team_recent_form: str = ""
    team_strength: str = ""
    team_weakness: str = ""
    
    # Rivalry context
    is_rivalry: bool = False
    rivalry_name: str = ""
    rivalry_encounters: int = 0
    rivalry_record: str = ""
    last_encounter_winner: str = ""
    
    # Storyline context
    active_storylines: List[str] = field(default_factory=list)
    storyline_importance: float = 0.0
    
    # Clutch context
    is_clutch: bool = False
    clutch_type: str = ""  # 1v2, 1v3, 1v4
    clutch_probability: float = 0.5
    enemies_remaining: int = 0
    
    # Fight context
    is_fight: bool = False
    fight_intensity: float = 0.0
    fight_duration: float = 0.0
    fight_total_damage: int = 0
    fight_total_kills: int = 0
    
    # Zone context
    is_final_circle: bool = False
    zone_danger_level: float = 0.0
    teams_in_zone: int = 0
    
    # Historical context
    player_avg_kills: float = 0.0
    player_avg_damage: float = 0.0
    team_avg_placement: float = 0.0
    head_to_head_wins: int = 0
    head_to_head_losses: int = 0
    
    # Broadcast context
    broadcast_style: str = "hinglish"
    broadcast_tone: str = "excited"
    target_language: str = "hinglish"
    
    # Recent events (for avoiding repetition)
    recent_commentary: List[str] = field(default_factory=list)
    recent_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Metadata
    timestamp: float = field(default_factory=time.time)
    match_id: str = ""
    tournament_id: str = ""
    
    def to_prompt_context(self) -> Dict[str, Any]:
        """Convert to dictionary for prompt filling"""
        return {
            "player": self.primary_player,
            "team": self.primary_team,
            "victim": self.secondary_player,
            "victim_team": self.secondary_team,
            "weapon": self.weapon,
            "damage": self.damage,
            "headshot": "HEADSHOT!" if self.headshot else "",
            "distance": f"{self.distance:.0f}m" if self.distance > 0 else "",
            "count": self.kill_count,
            "time": f"{self.time_span:.1f}" if self.time_span > 0 else "",
            "enemies": self.enemies_remaining,
            "hp": 100,  # Would be filled from actual data
            "armor": 100,
            "weapons": self.weapon,
            "zone_phase": self.zone_phase,
            "teams": self.teams_alive,
            "teams_alive": self.teams_alive,
            "intensity": f"{self.fight_intensity:.0f}" if self.is_fight else "",
            "duration": f"{self.fight_duration:.0f}s" if self.is_fight else "",
            "kills": self.player_kills,
            "player_kills": self.player_kills,
            "team_kills": self.team_kills,
            "placement": self.team_placement,
            "eliminator": self.primary_team if self.event_type == "team_wipe" else "",
            "rivalry": self.rivalry_name if self.is_rivalry else "",
            "clutch_type": self.clutch_type if self.is_clutch else "",
            "probability": f"{self.clutch_probability:.0%}" if self.is_clutch else "",
        }
    
    def get_rich_description(self) -> str:
        """Get rich text description of the context"""
        parts = []
        
        # Event description
        if self.event_description:
            parts.append(self.event_description)
        
        # Player context
        if self.primary_player:
            player_info = f"{self.primary_player}"
            if self.primary_team:
                player_info += f" from {self.primary_team}"
            if self.player_kills > 0:
                player_info += f" ({self.player_kills} kills this match)"
            if self.player_playstyle:
                player_info += f" - {self.player_playstyle}"
            parts.append(player_info)
        
        # Rivalry context
        if self.is_rivalry and self.rivalry_name:
            parts.append(f"Rivalry: {self.rivalry_name} ({self.rivalry_record})")
        
        # Clutch context
        if self.is_clutch:
            parts.append(f"Clutch situation: {self.clutch_type} ({self.clutch_probability:.0%} win probability)")
        
        # Match context
        parts.append(f"Zone {self.zone_phase} - {self.teams_alive} teams alive")
        
        return " | ".join(parts)


class ContextBuilder:
    """
    Builds rich commentary context from multiple data sources.
    
    Combines:
    - Current match state (zone, teams, score)
    - Player performance data
    - Team statistics and playstyle
    - Historical rivalries
    - Active storylines
    - Recent events
    
    Provides enriched context for more engaging commentary.
    """
    
    def __init__(self):
        # Player database
        self.player_stats: Dict[str, Dict[str, Any]] = {}
        self.player_profiles: Dict[str, Dict[str, Any]] = {}
        
        # Team database
        self.team_stats: Dict[str, Dict[str, Any]] = {}
        self.team_profiles: Dict[str, Dict[str, Any]] = {}
        
        # Rivalry database
        self.rivalries: Dict[str, Dict[str, Any]] = {}
        
        # Storylines
        self.active_storylines: List[Dict[str, Any]] = []
        
        # Recent events
        self.recent_events: deque = deque(maxlen=50)
        
        # Match state
        self.match_state: Dict[str, Any] = {}
        
        self._log = logger.bind(component="context_builder")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA LOADING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def load_player_stats(self, player_id: str, stats: Dict[str, Any]) -> None:
        """Load player statistics"""
        self.player_stats[player_id] = stats
    
    def load_player_profile(self, player_id: str, profile: Dict[str, Any]) -> None:
        """Load player profile (playstyle, weapons, etc.)"""
        self.player_profiles[player_id] = profile
    
    def load_team_stats(self, team_id: str, stats: Dict[str, Any]) -> None:
        """Load team statistics"""
        self.team_stats[team_id] = stats
    
    def load_team_profile(self, team_id: str, profile: Dict[str, Any]) -> None:
        """Load team profile"""
        self.team_profiles[team_id] = profile
    
    def load_rivalry(self, team_a: str, team_b: str, rivalry_data: Dict[str, Any]) -> None:
        """Load rivalry data between two teams"""
        key = self._get_rivalry_key(team_a, team_b)
        self.rivalries[key] = rivalry_data
    
    def set_active_storylines(self, storylines: List[Dict[str, Any]]) -> None:
        """Set active storylines"""
        self.active_storylines = storylines
    
    def update_match_state(self, state: Dict[str, Any]) -> None:
        """Update current match state"""
        self.match_state.update(state)
    
    def add_recent_event(self, event: Dict[str, Any]) -> None:
        """Add a recent event"""
        self.recent_events.append(event)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONTEXT BUILDING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def build_context(
        self,
        event_type: str,
        event_data: Dict[str, Any],
        match_id: str = "",
        tournament_id: str = "",
    ) -> CommentaryContext:
        """
        Build complete commentary context from an event.
        
        Args:
            event_type: Type of event (kill, clutch, fight, etc.)
            event_data: Event data
            match_id: Current match ID
            tournament_id: Current tournament ID
            
        Returns:
            Rich CommentaryContext for commentary generation
        """
        
        context = CommentaryContext(
            event_type=event_type,
            match_id=match_id,
            tournament_id=tournament_id,
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # EVENT DETAILS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        context.event_description = event_data.get("description", event_type)
        context.weapon = event_data.get("weapon", "")
        context.damage = event_data.get("damage", 0)
        context.headshot = event_data.get("headshot", False)
        context.distance = event_data.get("distance", 0.0)
        context.kill_count = event_data.get("kill_count", 1)
        context.time_span = event_data.get("time_span", 0.0)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PARTICIPANTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        context.primary_player = event_data.get("player_name", "")
        context.primary_player_id = event_data.get("player_id", "")
        context.primary_team = event_data.get("team_name", "")
        context.primary_team_id = event_data.get("team_id", "")
        
        context.secondary_player = event_data.get("target_name", "")
        context.secondary_player_id = event_data.get("target_id", "")
        context.secondary_team = event_data.get("target_team_name", "")
        context.secondary_team_id = event_data.get("target_team_id", "")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # MATCH STATE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        context.zone_phase = self.match_state.get("zone_phase", event_data.get("zone_phase", 0))
        context.teams_alive = self.match_state.get("teams_alive", event_data.get("teams_alive", 16))
        context.players_alive = self.match_state.get("players_alive", context.teams_alive * 4)
        context.match_time = self.match_state.get("match_time", 0)
        context.match_name = self.match_state.get("match_name", "")
        context.tournament_name = self.match_state.get("tournament_name", "")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PLAYER CONTEXT
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if context.primary_player_id:
            self._enrich_player_context(context, context.primary_player_id)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # TEAM CONTEXT
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if context.primary_team_id:
            self._enrich_team_context(context, context.primary_team_id)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # RIVALRY CONTEXT
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if context.primary_team_id and context.secondary_team_id:
            self._enrich_rivalry_context(context)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # STORYLINE CONTEXT
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        self._enrich_storyline_context(context)
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # SPECIAL SITUATIONS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        if event_type in ["clutch_start", "clutch_win", "clutch_fail"]:
            context.is_clutch = True
            context.clutch_type = event_data.get("clutch_type", "1v4")
            context.clutch_probability = event_data.get("probability", 0.1)
            context.enemies_remaining = event_data.get("enemies_remaining", 4)
        
        if event_type in ["fight_start", "fight_intense", "fight_end"]:
            context.is_fight = True
            context.fight_intensity = event_data.get("intensity_score", 50)
            context.fight_duration = event_data.get("duration", 0)
            context.fight_total_damage = event_data.get("total_damage", 0)
            context.fight_total_kills = event_data.get("total_kills", 0)
        
        if context.zone_phase >= 6:
            context.is_final_circle = True
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # RECENT EVENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        context.recent_events = list(self.recent_events)[-10:]
        
        return context
    
    def _enrich_player_context(self, context: CommentaryContext, player_id: str) -> None:
        """Enrich context with player data"""
        
        # Stats
        stats = self.player_stats.get(player_id, {})
        context.player_kills = stats.get("kills", 0)
        context.player_damage = stats.get("damage", 0)
        context.player_knocks = stats.get("knocks", 0)
        context.player_mvp_count = stats.get("mvp_count", 0)
        context.player_avg_kills = stats.get("avg_kills", 0.0)
        context.player_avg_damage = stats.get("avg_damage", 0.0)
        
        # Profile
        profile = self.player_profiles.get(player_id, {})
        context.player_playstyle = profile.get("playstyle", "")
        context.player_signature_weapon = profile.get("signature_weapon", "")
        context.player_clutches = profile.get("clutch_count", 0)
        context.player_recent_form = profile.get("recent_form", "")
    
    def _enrich_team_context(self, context: CommentaryContext, team_id: str) -> None:
        """Enrich context with team data"""
        
        # Stats
        stats = self.team_stats.get(team_id, {})
        context.team_kills = stats.get("kills", 0)
        context.team_damage = stats.get("damage", 0)
        context.team_placement = stats.get("placement", 0)
        context.team_avg_placement = stats.get("avg_placement", 0.0)
        
        # Profile
        profile = self.team_profiles.get(team_id, {})
        context.team_playstyle = profile.get("playstyle", "")
        context.team_recent_form = profile.get("recent_form", "")
        context.team_strength = profile.get("strength", "")
        context.team_weakness = profile.get("weakness", "")
    
    def _enrich_rivalry_context(self, context: CommentaryContext) -> None:
        """Enrich context with rivalry data"""
        
        key = self._get_rivalry_key(context.primary_team_id, context.secondary_team_id)
        rivalry = self.rivalries.get(key, {})
        
        if rivalry:
            context.is_rivalry = True
            context.rivalry_name = rivalry.get("name", "")
            context.rivalry_encounters = rivalry.get("encounters", 0)
            
            team_a_wins = rivalry.get(f"{context.primary_team_id}_wins", 0)
            team_b_wins = rivalry.get(f"{context.secondary_team_id}_wins", 0)
            context.rivalry_record = f"{team_a_wins}-{team_b_wins}"
            context.last_encounter_winner = rivalry.get("last_winner", "")
            context.head_to_head_wins = team_a_wins
            context.head_to_head_losses = team_b_wins
    
    def _enrich_storyline_context(self, context: CommentaryContext) -> None:
        """Enrich context with active storylines"""
        
        relevant = []
        max_importance = 0.0
        
        for storyline in self.active_storylines:
            # Check if storyline involves these teams/players
            teams = storyline.get("teams", [])
            players = storyline.get("players", [])
            
            if (context.primary_team_id in teams or 
                context.secondary_team_id in teams or
                context.primary_player_id in players or
                context.secondary_player_id in players):
                
                relevant.append(storyline.get("title", ""))
                max_importance = max(max_importance, storyline.get("importance", 0.0))
        
        context.active_storylines = relevant
        context.storyline_importance = max_importance
    
    def _get_rivalry_key(self, team_a: str, team_b: str) -> str:
        """Get consistent rivalry key"""
        return "_".join(sorted([team_a, team_b]))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUICK CONTEXT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def build_quick_context(
        self,
        player_name: str = "",
        team_name: str = "",
        event_description: str = "",
    ) -> CommentaryContext:
        """Build minimal context quickly"""
        
        return CommentaryContext(
            event_description=event_description,
            primary_player=player_name,
            primary_team=team_name,
            zone_phase=self.match_state.get("zone_phase", 0),
            teams_alive=self.match_state.get("teams_alive", 16),
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get context builder statistics"""
        return {
            "players_loaded": len(self.player_stats),
            "teams_loaded": len(self.team_stats),
            "rivalries_loaded": len(self.rivalries),
            "active_storylines": len(self.active_storylines),
            "recent_events": len(self.recent_events),
        }
    
    def reset(self) -> None:
        """Reset context builder"""
        self.player_stats.clear()
        self.player_profiles.clear()
        self.team_stats.clear()
        self.team_profiles.clear()
        self.rivalries.clear()
        self.active_storylines.clear()
        self.recent_events.clear()
        self.match_state.clear()
        self._log.info("Context builder reset")