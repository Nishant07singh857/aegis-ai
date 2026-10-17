# Auto-generated placeholder for pov_selector.py
# backend/app/agents/observer_agents/pov_selector.py
"""
AEGIS AI - POV Selector
Selects the best camera angle/feed for broadcasting.
Considers fight importance, player status, entertainment value,
and broadcast priorities.
"""

import time
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.pov_selector")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FeedType(str, Enum):
    """Types of broadcast feeds"""
    TEAM_POV = "team_pov"           # Team perspective
    PLAYER_POV = "player_pov"       # Single player perspective
    FIGHT = "fight"                 # Active fight coverage
    MAP_OVERVIEW = "map_overview"   # Tactical map view
    FINAL_ZONE = "final_zone"       # Final circle overview
    CINEMATIC = "cinematic"         # Cinematic camera
    REPLAY = "replay"              # Replay footage
    SPLIT_SCREEN = "split_screen"  # Multiple feeds
    FOLLOW_ACTION = "follow_action" # Follow the action
    SPECTATOR_DRONE = "spectator_drone" # Free camera

class CameraMode(str, Enum):
    """Camera operation modes"""
    FOLLOW = "follow"           # Follow player/team
    FIRST_PERSON = "first_person" # First person view
    THIRD_PERSON = "third_person" # Third person view
    OVERHEAD = "overhead"       # Top-down view
    CINEMATIC = "cinematic"     # Smooth cinematic
    CLUTCH = "clutch"          # Clutch-focused
    DYNAMIC = "dynamic"        # AI-controlled dynamic
    STATIC = "static"          # Fixed position
    ORBIT = "orbit"           # Orbit around target

class POVPriority(str, Enum):
    """Priority levels for POV selection"""
    CRITICAL = "critical"     # Must show (clutch, final fight)
    HIGH = "high"            # Important (active fight)
    MEDIUM = "medium"        # Interesting (star player)
    LOW = "low"              # Filler content
    BACKGROUND = "background" # Not for broadcast

@dataclass
class POVCandidate:
    """A candidate for broadcast feed"""
    feed_type: FeedType
    priority: POVPriority = POVPriority.MEDIUM
    priority_score: float = 50.0
    
    # Target
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    fight_id: Optional[str] = None
    
    # Camera
    camera_mode: CameraMode = CameraMode.FOLLOW
    transition_duration: float = 0.5
    
    # Context
    reason: str = ""
    entertainment_score: float = 0.0
    narrative_score: float = 0.0
    
    # State
    is_clutch: bool = False
    is_fight: bool = False
    is_final_circle: bool = False
    is_star_player: bool = False
    is_rivalry: bool = False
    
    # Location
    location_x: float = 0.0
    location_y: float = 0.0
    
    # Timing
    created_at: float = field(default_factory=time.time)
    expires_at: float = 0.0
    min_duration: float = 5.0  # Minimum seconds to show
    
    def is_valid(self) -> bool:
        """Check if candidate is still valid"""
        if self.expires_at > 0 and time.time() > self.expires_at:
            return False
        return True
    
    def get_age(self) -> float:
        """Get age of candidate in seconds"""
        return time.time() - self.created_at

@dataclass
class TeamFeedStatus:
    """Status of a team's feed"""
    team_id: str
    team_name: str
    alive_count: int = 4
    is_fighting: bool = False
    fight_intensity: float = 0.0
    has_clutch: bool = False
    last_shown_time: float = 0.0
    times_shown: int = 0
    priority_modifier: float = 0.0  # Can be boosted for priority teams

@dataclass
class PlayerFeedStatus:
    """Status of a player's feed"""
    player_id: str
    player_name: str
    team_id: str
    kills: int = 0
    damage: int = 0
    is_alive: bool = True
    is_clutch: bool = False
    last_shown_time: float = 0.0
    times_shown: int = 0
    star_rating: float = 0.0  # 0-10 star power


class POVSelector(BaseAgent):
    """
    Selects the best POV/feed for broadcast.
    
    Selection Criteria:
    1. Active fights (highest priority)
    2. Clutch situations
    3. Star players
    4. Rivalry matchups
    5. Final circle positioning
    6. Entertainment value
    7. Narrative importance
    8. Viewing variety (not showing same team too long)
    
    Balances between:
    - Showing important action
    - Maintaining viewer engagement
    - Following storylines
    - Avoiding repetitive coverage
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Team feed tracking
        self.team_feeds: Dict[str, TeamFeedStatus] = {}
        
        # Player feed tracking
        self.player_feeds: Dict[str, PlayerFeedStatus] = {}
        
        # Priority teams/players
        self.priority_teams: Set[str] = set()
        self.priority_players: Set[str] = set()
        self.star_players: Dict[str, float] = {}  # player_id -> star_rating
        
        # Viewing history
        self.recently_shown_teams: deque = deque(maxlen=20)
        self.recently_shown_players: deque = deque(maxlen=30)
        
        # Configuration
        self.min_time_between_team_shows = 15  # seconds
        self.min_time_between_player_shows = 30  # seconds
        self.max_consecutive_same_team = 3
        
        # Weights for scoring
        self.weights = {
            "fight_intensity": 0.30,
            "clutch_bonus": 0.25,
            "star_power": 0.15,
            "entertainment": 0.15,
            "narrative": 0.10,
            "variety": 0.05,
        }
        
        self._log = logger.bind(component="pov_selector")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CANDIDATE GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_candidates(
        self,
        director_state: Any,
        active_fights: Dict[str, Any],
    ) -> List[POVCandidate]:
        """
        Get all possible broadcast candidates.
        Returns list sorted by priority score.
        """
        candidates = []
        
        # Add fight candidates (highest priority)
        candidates.extend(self._get_fight_candidates(active_fights, director_state))
        
        # Add clutch candidates
        candidates.extend(self._get_clutch_candidates(director_state))
        
        # Add star player candidates
        candidates.extend(self._get_star_player_candidates(director_state))
        
        # Add team candidates
        candidates.extend(self._get_team_candidates(director_state))
        
        # Add map overview (for final circles)
        if director_state.zone_phase >= 6:
            candidates.extend(self._get_final_circle_candidates(director_state))
        
        # Add cinematic candidates (for quiet moments)
        if len(candidates) < 3:
            candidates.extend(self._get_cinematic_candidates(director_state))
        
        # Filter invalid candidates
        candidates = [c for c in candidates if c.is_valid()]
        
        # Sort by priority score
        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        
        return candidates
    
    def _get_fight_candidates(
        self,
        active_fights: Dict[str, Any],
        director_state: Any,
    ) -> List[POVCandidate]:
        """Generate candidates from active fights"""
        candidates = []
        
        for fight_id, fight in active_fights.items():
            teams = fight.get("teams", [])
            intensity = fight.get("intensity", "medium")
            intensity_score = fight.get("intensity_score", 50)
            entertainment = fight.get("entertainment_score", 50)
            is_clutch = fight.get("is_clutch", False)
            
            # Determine priority
            if is_clutch:
                priority = POVPriority.CRITICAL
                priority_score = 95
            elif intensity_score > 70:
                priority = POVPriority.HIGH
                priority_score = intensity_score
            elif intensity_score > 40:
                priority = POVPriority.MEDIUM
                priority_score = intensity_score
            else:
                priority = POVPriority.LOW
                priority_score = intensity_score
            
            # Boost for priority teams
            for team_id in teams:
                if team_id in self.priority_teams:
                    priority_score += 10
                    if priority == POVPriority.LOW:
                        priority = POVPriority.MEDIUM
            
            # Boost for rivalry
            if self._is_rivalry_fight(teams):
                priority_score += 15
                if priority == POVPriority.LOW:
                    priority = POVPriority.MEDIUM
            
            # Select best team to show
            best_team = self._select_best_team_in_fight(fight_id, teams, director_state)
            
            candidate = POVCandidate(
                feed_type=FeedType.FIGHT,
                priority=priority,
                priority_score=priority_score,
                fight_id=fight_id,
                team_id=best_team,
                camera_mode=CameraMode.DYNAMIC,
                reason=f"Active fight ({intensity})",
                entertainment_score=entertainment,
                is_clutch=is_clutch,
                is_fight=True,
                min_duration=6.0,
            )
            
            # Determine camera mode based on fight characteristics
            if is_clutch:
                candidate.camera_mode = CameraMode.CLUTCH
                candidate.min_duration = 8.0
            elif intensity_score > 70:
                candidate.camera_mode = CameraMode.DYNAMIC
                candidate.min_duration = 8.0
            else:
                candidate.camera_mode = CameraMode.THIRD_PERSON
                candidate.min_duration = 5.0
            
            candidates.append(candidate)
        
        return candidates
    
    def _get_clutch_candidates(self, director_state: Any) -> List[POVCandidate]:
        """Generate candidates from clutch situations"""
        candidates = []
        
        # This would get active clutches from clutch detector
        # For now, check active fights for clutch situations
        for fight_id, fight in director_state.active_fights.items():
            if fight.get("is_clutch"):
                candidate = POVCandidate(
                    feed_type=FeedType.PLAYER_POV,
                    priority=POVPriority.CRITICAL,
                    priority_score=100,
                    fight_id=fight_id,
                    camera_mode=CameraMode.CLUTCH,
                    reason="Clutch situation",
                    entertainment_score=95,
                    is_clutch=True,
                    min_duration=10.0,
                )
                candidates.append(candidate)
        
        return candidates
    
    def _get_star_player_candidates(self, director_state: Any) -> List[POVCandidate]:
        """Generate candidates for star players"""
        candidates = []
        
        for player_id, star_rating in self.star_players.items():
            # Check if player hasn't been shown recently
            if player_id in self.recently_shown_players:
                continue
            
            # Check if player is alive and doing well
            player_feed = self.player_feeds.get(player_id)
            if not player_feed or not player_feed.is_alive:
                continue
            
            # Calculate priority
            priority_score = 40 + star_rating * 5
            
            # Boost if player has kills
            if player_feed.kills >= 3:
                priority_score += 15
            if player_feed.kills >= 5:
                priority_score += 10
            
            candidate = POVCandidate(
                feed_type=FeedType.PLAYER_POV,
                priority=POVPriority.MEDIUM if priority_score > 60 else POVPriority.LOW,
                priority_score=priority_score,
                player_id=player_id,
                team_id=player_feed.team_id,
                camera_mode=CameraMode.FIRST_PERSON if star_rating > 7 else CameraMode.THIRD_PERSON,
                reason=f"Star player: {player_feed.player_name}",
                entertainment_score=star_rating * 10,
                is_star_player=True,
                min_duration=5.0,
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _get_team_candidates(self, director_state: Any) -> List[POVCandidate]:
        """Generate candidates for team coverage"""
        candidates = []
        
        for team_id, team_feed in self.team_feeds.items():
            # Skip eliminated teams
            if team_feed.alive_count == 0:
                continue
            
            # Skip recently shown teams
            if team_id in self.recently_shown_teams:
                continue
            
            # Skip teams already in a fight (covered by fight candidates)
            if team_feed.is_fighting:
                continue
            
            # Calculate priority
            priority_score = 30  # Base
            
            # Boost for surviving teams (fewer teams = more important)
            if director_state.teams_alive <= 5:
                priority_score += 25
            elif director_state.teams_alive <= 8:
                priority_score += 15
            elif director_state.teams_alive <= 12:
                priority_score += 5
            
            # Boost for teams with kills
            priority_score += min(10, team_feed.fight_intensity * 2)
            
            # Boost for priority teams
            if team_id in self.priority_teams:
                priority_score += 15
            
            candidate = POVCandidate(
                feed_type=FeedType.TEAM_POV,
                priority=POVPriority.MEDIUM if priority_score > 50 else POVPriority.LOW,
                priority_score=priority_score,
                team_id=team_id,
                camera_mode=CameraMode.THIRD_PERSON,
                reason=f"Team coverage: {team_feed.team_name}",
                min_duration=5.0,
            )
            
            candidates.append(candidate)
        
        return candidates
    
    def _get_final_circle_candidates(self, director_state: Any) -> List[POVCandidate]:
        """Generate candidates for final circle coverage"""
        candidates = []
        
        # Map overview for final circle
        candidate = POVCandidate(
            feed_type=FeedType.MAP_OVERVIEW,
            priority=POVPriority.HIGH,
            priority_score=80 if director_state.teams_alive <= 3 else 65,
            camera_mode=CameraMode.OVERHEAD,
            reason=f"Final circle - {director_state.teams_alive} teams alive",
            is_final_circle=True,
            min_duration=8.0,
        )
        candidates.append(candidate)
        
        # Final zone overview
        if director_state.teams_alive <= 5:
            candidate = POVCandidate(
                feed_type=FeedType.FINAL_ZONE,
                priority=POVPriority.HIGH,
                priority_score=75,
                camera_mode=CameraMode.DYNAMIC,
                reason="Final zone positioning",
                is_final_circle=True,
                min_duration=6.0,
            )
            candidates.append(candidate)
        
        return candidates
    
    def _get_cinematic_candidates(self, director_state: Any) -> List[POVCandidate]:
        """Generate cinematic camera candidates for quiet moments"""
        candidates = []
        
        # Cinematic overview
        candidate = POVCandidate(
            feed_type=FeedType.CINEMATIC,
            priority=POVPriority.LOW,
            priority_score=25,
            camera_mode=CameraMode.CINEMATIC,
            reason="Cinematic overview",
            min_duration=8.0,
        )
        candidates.append(candidate)
        
        # Spectator drone
        candidate = POVCandidate(
            feed_type=FeedType.SPECTATOR_DRONE,
            priority=POVPriority.LOW,
            priority_score=20,
            camera_mode=CameraMode.ORBIT,
            reason="Map flyover",
            min_duration=10.0,
        )
        candidates.append(candidate)
        
        return candidates
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BEST CANDIDATE SELECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def select_best_candidate(
        self,
        candidates: List[POVCandidate],
        director_state: Any,
    ) -> Optional[POVCandidate]:
        """
        Select the single best candidate to show.
        Uses multi-factor scoring with context awareness.
        """
        if not candidates:
            return None
        
        # Score each candidate
        scored_candidates = []
        for candidate in candidates:
            score = self._score_candidate(candidate, director_state)
            scored_candidates.append((score, candidate))
        
        # Sort by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        
        # Return best candidate
        if scored_candidates:
            best_score, best_candidate = scored_candidates[0]
            best_candidate.priority_score = best_score
            return best_candidate
        
        return None
    
    def _score_candidate(
        self,
        candidate: POVCandidate,
        director_state: Any,
    ) -> float:
        """Score a candidate with comprehensive factors"""
        score = candidate.priority_score
        
        # Fight intensity bonus
        if candidate.is_fight:
            score += self.weights["fight_intensity"] * 100
        
        # Clutch bonus (highest)
        if candidate.is_clutch:
            score += self.weights["clutch_bonus"] * 100
        
        # Star power bonus
        if candidate.is_star_player:
            star_rating = self.star_players.get(candidate.player_id or "", 5)
            score += self.weights["star_power"] * star_rating * 10
        
        # Entertainment bonus
        score += self.weights["entertainment"] * candidate.entertainment_score
        
        # Narrative bonus
        if candidate.is_rivalry:
            score += self.weights["narrative"] * 100
        
        # Variety bonus (penalize recently shown)
        variety_penalty = 0
        if candidate.team_id:
            if candidate.team_id in self.recently_shown_teams:
                position = list(self.recently_shown_teams).index(candidate.team_id)
                variety_penalty = (len(self.recently_shown_teams) - position) * 2
        
        if candidate.player_id:
            if candidate.player_id in self.recently_shown_players:
                position = list(self.recently_shown_players).index(candidate.player_id)
                variety_penalty = max(variety_penalty, (len(self.recently_shown_players) - position) * 2)
        
        score -= variety_penalty * self.weights["variety"]
        
        # Final circle bonus
        if candidate.is_final_circle:
            score += 15
        
        # Zone phase bonus (later zones = more important)
        if director_state.zone_phase >= 6:
            score += 10
        
        # Few teams alive = more focus
        if director_state.teams_alive <= 3:
            score += 15
        
        # Priority team/player bonus
        if candidate.team_id in self.priority_teams:
            score += 10
        if candidate.player_id in self.priority_players:
            score += 15
        
        return max(0, min(100, score))
    
    def select_best_team_for_fight(
        self,
        fight_id: str,
        teams: List[str],
        director_state: Any,
    ) -> Optional[str]:
        """Select the best team to show for a fight"""
        if not teams:
            return None
        
        best_team = None
        best_score = -1
        
        for team_id in teams:
            score = 50  # Base
            
            # Prefer team that hasn't been shown recently
            if team_id not in self.recently_shown_teams:
                score += 20
            
            # Prefer priority teams
            if team_id in self.priority_teams:
                score += 15
            
            # Prefer team with more players alive (more action)
            team_feed = self.team_feeds.get(team_id)
            if team_feed:
                score += team_feed.alive_count * 5
            
            if score > best_score:
                best_score = score
                best_team = team_id
        
        return best_team
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRACKING & UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_feed_shown(self, candidate: POVCandidate) -> None:
        """Record that a feed was shown"""
        if candidate.team_id:
            self.recently_shown_teams.append(candidate.team_id)
            
            if candidate.team_id in self.team_feeds:
                self.team_feeds[candidate.team_id].last_shown_time = time.time()
                self.team_feeds[candidate.team_id].times_shown += 1
        
        if candidate.player_id:
            self.recently_shown_players.append(candidate.player_id)
            
            if candidate.player_id in self.player_feeds:
                self.player_feeds[candidate.player_id].last_shown_time = time.time()
                self.player_feeds[candidate.player_id].times_shown += 1
    
    def update_team_feed(
        self,
        team_id: str,
        team_name: str,
        alive_count: int,
        is_fighting: bool = False,
        fight_intensity: float = 0.0,
    ) -> None:
        """Update team feed status"""
        if team_id not in self.team_feeds:
            self.team_feeds[team_id] = TeamFeedStatus(
                team_id=team_id,
                team_name=team_name,
            )
        
        feed = self.team_feeds[team_id]
        feed.alive_count = alive_count
        feed.is_fighting = is_fighting
        feed.fight_intensity = fight_intensity
    
    def update_player_feed(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        kills: int = 0,
        damage: int = 0,
        is_alive: bool = True,
    ) -> None:
        """Update player feed status"""
        if player_id not in self.player_feeds:
            self.player_feeds[player_id] = PlayerFeedStatus(
                player_id=player_id,
                player_name=player_name,
                team_id=team_id,
            )
        
        feed = self.player_feeds[player_id]
        feed.kills = kills
        feed.damage = damage
        feed.is_alive = is_alive
        
        # Update star rating
        if kills >= 5:
            feed.star_rating = min(10, feed.star_rating + 1)
        if damage > 2000:
            feed.star_rating = min(10, feed.star_rating + 0.5)
    
    def set_star_player(self, player_id: str, rating: float) -> None:
        """Set a player as a star player"""
        self.star_players[player_id] = rating
        self.priority_players.add(player_id)
        self._log.info(f"Star player set: {player_id} (rating: {rating})")
    
    def set_priority_team(self, team_id: str) -> None:
        """Set a team as priority"""
        self.priority_teams.add(team_id)
        self._log.info(f"Priority team set: {team_id}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _is_rivalry_fight(self, teams: List[str]) -> bool:
        """Check if this is a rivalry fight"""
        # Would check against rivalry database
        # For now, return True if priority teams are involved
        return len(set(teams) & self.priority_teams) >= 2
    
    def _select_best_team_in_fight(
        self,
        fight_id: str,
        teams: List[str],
        director_state: Any,
    ) -> Optional[str]:
        """Select best team POV in a fight"""
        return self.select_best_team_for_fight(fight_id, teams, director_state)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get POV selector statistics"""
        return {
            "teams_tracked": len(self.team_feeds),
            "players_tracked": len(self.player_feeds),
            "star_players": len(self.star_players),
            "priority_teams": len(self.priority_teams),
            "recent_teams": list(self.recently_shown_teams),
            "recent_players": list(self.recently_shown_players),
        }
    
    def reset(self) -> None:
        """Reset selector state"""
        self.recently_shown_teams.clear()
        self.recently_shown_players.clear()
        self._log.info("POV selector reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("fight_detected")
        self.subscribe_to_event("clutch_detected")

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
