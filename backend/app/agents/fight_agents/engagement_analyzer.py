# backend/app/agents/fight_agents/engagement_analyzer.py
"""
AEGIS AI - Engagement Analyzer
Analyzes team fight engagements, weapon usage patterns,
effective range analysis, and team synergy in combat.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque, defaultdict
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.engagement_analyzer")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EngagementOutcome(str, Enum):
    """Outcome of an engagement"""
    WIN = "win"
    LOSS = "loss"
    TRADE = "trade"
    DISENGAGE = "disengage"
    THIRD_PARTIED = "third_partied"
    ONGOING = "ongoing"

class WeaponCategory(str, Enum):
    """Weapon categories"""
    ASSAULT_RIFLE = "assault_rifle"
    SMG = "smg"
    SNIPER = "sniper"
    DMR = "dmr"
    SHOTGUN = "shotgun"
    LMG = "lmg"
    PISTOL = "pistol"
    THROWABLE = "throwable"
    MELEE = "melee"

class PlayerRole(str, Enum):
    """Player combat role"""
    ENTRY_FRAGGER = "entry_fragger"
    SUPPORT = "support"
    SNIPER = "sniper"
    IGL = "igl"
    FLEX = "flex"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class WeaponStats:
    """Statistics for a specific weapon"""
    weapon_name: str
    category: WeaponCategory
    kills: int = 0
    knocks: int = 0
    damage: int = 0
    headshots: int = 0
    shots_fired: int = 0
    shots_hit: int = 0
    accuracy: float = 0.0
    avg_damage_per_shot: float = 0.0
    effective_range: float = 0.0  # Average engagement distance
    
    def update_accuracy(self) -> None:
        """Update accuracy percentage"""
        if self.shots_fired > 0:
            self.accuracy = (self.shots_hit / self.shots_fired) * 100
            self.avg_damage_per_shot = self.damage / max(1, self.shots_hit)

@dataclass
class PlayerEngagementStats:
    """Per-player engagement statistics"""
    player_id: str
    player_name: str
    
    # Combat stats
    total_engagements: int = 0
    engagements_won: int = 0
    engagements_lost: int = 0
    
    # Kill stats
    total_kills: int = 0
    total_knocks: int = 0
    total_assists: int = 0
    total_damage: int = 0
    total_headshots: int = 0
    
    # Weapon proficiency
    weapon_stats: Dict[str, WeaponStats] = field(default_factory=dict)
    favorite_weapon: Optional[str] = None
    best_weapon: Optional[str] = None
    
    # Range preference
    avg_engagement_distance: float = 0.0
    close_range_kills: int = 0
    medium_range_kills: int = 0
    long_range_kills: int = 0
    
    # Role assessment
    primary_role: PlayerRole = PlayerRole.FLEX
    role_confidence: float = 0.0
    
    # Accuracy
    overall_accuracy: float = 0.0
    headshot_percentage: float = 0.0
    
    # Survival
    avg_survival_in_fights: float = 0.0
    first_blood_count: int = 0
    first_death_count: int = 0
    
    # Streaks
    current_kill_streak: int = 0
    best_kill_streak: int = 0
    
    matches_analyzed: int = 0

@dataclass
class TeamEngagementStats:
    """Team-wide engagement statistics"""
    team_id: str
    team_name: str
    
    # Overall
    total_fights: int = 0
    fights_won: int = 0
    fights_lost: int = 0
    win_rate: float = 0.0
    
    # Combat
    total_kills: int = 0
    total_damage: int = 0
    avg_kills_per_fight: float = 0.0
    avg_damage_per_fight: float = 0.0
    
    # Range preference
    preferred_engagement_range: str = "medium"
    close_range_preference: float = 0.0
    long_range_preference: float = 0.0
    
    # Team synergy
    trade_kill_rate: float = 0.0  # How often team trades kills
    refrag_rate: float = 0.0  # How often team kills enemy who killed teammate
    assist_rate: float = 0.0
    
    # Utility effectiveness
    grenade_effectiveness: float = 0.0
    smoke_effectiveness: float = 0.0
    
    # Weapon distribution
    team_weapon_profile: Dict[str, int] = field(default_factory=dict)
    
    # Performance against specific teams
    opponent_stats: Dict[str, Dict[str, Any]] = field(default_factory=dict)

@dataclass
class EngagementEvent:
    """Single engagement event"""
    event_id: str
    fight_id: Optional[str]
    
    # Participants
    player_id: str
    player_team_id: str
    target_id: Optional[str] = None
    target_team_id: Optional[str] = None
    
    # Combat details
    weapon: str = "unknown"
    damage: int = 0
    distance: float = 0.0
    headshot: bool = False
    is_knock: bool = False
    is_kill: bool = False
    
    # Position
    player_x: float = 0.0
    player_y: float = 0.0
    target_x: float = 0.0
    target_y: float = 0.0
    
    # Context
    zone_phase: int = 1
    is_third_party: bool = False
    timestamp: float = field(default_factory=time.time)


class EngagementAnalyzer(BaseAgent):
    """
    Analyzes team fight engagements in detail.
    
    Tracks:
    - Weapon usage and effectiveness
    - Engagement ranges and preferences
    - Player roles based on combat behavior
    - Team synergy (trades, refrags, assists)
    - Opponent-specific performance
    - Accuracy and headshot analysis
    - Kill streaks and first blood/death patterns
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Player stats
        self.player_stats: Dict[str, PlayerEngagementStats] = {}
        
        # Team stats
        self.team_stats: Dict[str, TeamEngagementStats] = {}
        
        # Recent engagements buffer
        self.recent_engagements: deque = deque(maxlen=200)
        
        # Weapon database
        self.weapon_categories = {
            "M416": WeaponCategory.ASSAULT_RIFLE,
            "AKM": WeaponCategory.ASSAULT_RIFLE,
            "SCAR-L": WeaponCategory.ASSAULT_RIFLE,
            "GROZA": WeaponCategory.ASSAULT_RIFLE,
            "AUG": WeaponCategory.ASSAULT_RIFLE,
            "M762": WeaponCategory.ASSAULT_RIFLE,
            "UMP45": WeaponCategory.SMG,
            "UZI": WeaponCategory.SMG,
            "Vector": WeaponCategory.SMG,
            "MP5K": WeaponCategory.SMG,
            "AWM": WeaponCategory.SNIPER,
            "M24": WeaponCategory.SNIPER,
            "Kar98k": WeaponCategory.SNIPER,
            "Mini14": WeaponCategory.DMR,
            "SKS": WeaponCategory.DMR,
            "Mk14": WeaponCategory.DMR,
            "SLR": WeaponCategory.DMR,
            "S12K": WeaponCategory.SHOTGUN,
            "DBS": WeaponCategory.SHOTGUN,
            "DP-28": WeaponCategory.LMG,
            "M249": WeaponCategory.LMG,
        }
        
        # Range definitions (meters)
        self.range_definitions = {
            "close": (0, 25),
            "medium": (25, 100),
            "long": (100, 300),
            "extreme": (300, 9999),
        }
        
        self._log = logger.bind(component="engagement_analyzer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ENGAGEMENT RECORDING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_engagement(
        self,
        event_id: str,
        player_id: str,
        player_name: str,
        player_team_id: str,
        target_id: str,
        target_team_id: str,
        weapon: str,
        damage: int,
        distance: float,
        headshot: bool = False,
        is_knock: bool = False,
        is_kill: bool = False,
        player_x: float = 0,
        player_y: float = 0,
        target_x: float = 0,
        target_y: float = 0,
        zone_phase: int = 1,
        fight_id: Optional[str] = None,
        is_third_party: bool = False,
    ) -> None:
        """Record an engagement event"""
        
        event = EngagementEvent(
            event_id=event_id,
            fight_id=fight_id,
            player_id=player_id,
            player_team_id=player_team_id,
            target_id=target_id,
            target_team_id=target_team_id,
            weapon=weapon,
            damage=damage,
            distance=distance,
            headshot=headshot,
            is_knock=is_knock,
            is_kill=is_kill,
            player_x=player_x,
            player_y=player_y,
            target_x=target_x,
            target_y=target_y,
            zone_phase=zone_phase,
            is_third_party=is_third_party,
        )
        
        self.recent_engagements.append(event)
        
        # Initialize player stats if needed
        self._init_player_stats(player_id, player_name)
        self._init_team_stats(player_team_id, "")
        
        # Update player stats
        self._update_player_stats(event)
        
        # Update team stats
        self._update_team_stats(event)
        
        # Update weapon stats
        self._update_weapon_stats(event)
        
        # Check kill streak
        if is_kill:
            self._update_kill_streak(player_id, True)
    
    def record_fight_outcome(
        self,
        fight_id: str,
        team_id: str,
        outcome: EngagementOutcome,
        kills: int = 0,
        damage: int = 0,
        opponent_team_id: Optional[str] = None,
    ) -> None:
        """Record a fight outcome"""
        
        self._init_team_stats(team_id, "")
        stats = self.team_stats[team_id]
        
        stats.total_fights += 1
        
        if outcome == EngagementOutcome.WIN:
            stats.fights_won += 1
        elif outcome == EngagementOutcome.LOSS:
            stats.fights_lost += 1
        
        stats.total_kills += kills
        stats.total_damage += damage
        
        if stats.total_fights > 0:
            stats.win_rate = stats.fights_won / stats.total_fights
            stats.avg_kills_per_fight = stats.total_kills / stats.total_fights
            stats.avg_damage_per_fight = stats.total_damage / stats.total_fights
        
        # Update opponent stats
        if opponent_team_id:
            if opponent_team_id not in stats.opponent_stats:
                stats.opponent_stats[opponent_team_id] = {
                    "encounters": 0, "wins": 0, "losses": 0,
                    "kills": 0, "damage": 0,
                }
            
            opp_stats = stats.opponent_stats[opponent_team_id]
            opp_stats["encounters"] += 1
            if outcome == EngagementOutcome.WIN:
                opp_stats["wins"] += 1
            elif outcome == EngagementOutcome.LOSS:
                opp_stats["losses"] += 1
            opp_stats["kills"] += kills
            opp_stats["damage"] += damage
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATS UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _init_player_stats(self, player_id: str, player_name: str) -> None:
        """Initialize player stats if not exists"""
        if player_id not in self.player_stats:
            self.player_stats[player_id] = PlayerEngagementStats(
                player_id=player_id,
                player_name=player_name,
            )
    
    def _init_team_stats(self, team_id: str, team_name: str) -> None:
        """Initialize team stats if not exists"""
        if team_id not in self.team_stats:
            self.team_stats[team_id] = TeamEngagementStats(
                team_id=team_id,
                team_name=team_name,
            )
    
    def _update_player_stats(self, event: EngagementEvent) -> None:
        """Update player statistics from engagement"""
        stats = self.player_stats.get(event.player_id)
        if not stats:
            return
        
        stats.total_engagements += 1
        
        if event.is_kill:
            stats.total_kills += 1
            stats.engagements_won += 1
        elif event.is_knock:
            stats.total_knocks += 1
        
        stats.total_damage += event.damage
        
        if event.headshot:
            stats.total_headshots += 1
        
        # Update engagement distance average
        n = stats.total_engagements
        stats.avg_engagement_distance = (
            stats.avg_engagement_distance * (n - 1) + event.distance
        ) / n
        
        # Track range category
        if event.distance <= 25:
            stats.close_range_kills += 1
        elif event.distance <= 100:
            stats.medium_range_kills += 1
        else:
            stats.long_range_kills += 1
        
        # Update headshot percentage
        if stats.total_kills + stats.total_knocks > 0:
            stats.headshot_percentage = (
                stats.total_headshots / (stats.total_kills + stats.total_knocks) * 100
            )
        
        # Assess player role
        self._assess_player_role(stats)
    
    def _update_team_stats(self, event: EngagementEvent) -> None:
        """Update team statistics"""
        stats = self.team_stats.get(event.player_team_id)
        if not stats:
            return
        
        # Update weapon profile
        weapon = event.weapon
        if weapon not in stats.team_weapon_profile:
            stats.team_weapon_profile[weapon] = 0
        stats.team_weapon_profile[weapon] += 1
        
        # Update range preference
        if event.distance <= 25:
            stats.close_range_preference += 1
        elif event.distance > 100:
            stats.long_range_preference += 1
        
        total = stats.close_range_preference + stats.long_range_preference
        if total > 0:
            if stats.close_range_preference / total > 0.6:
                stats.preferred_engagement_range = "close"
            elif stats.long_range_preference / total > 0.6:
                stats.preferred_engagement_range = "long"
            else:
                stats.preferred_engagement_range = "medium"
    
    def _update_weapon_stats(self, event: EngagementEvent) -> None:
        """Update weapon-specific statistics"""
        stats = self.player_stats.get(event.player_id)
        if not stats:
            return
        
        weapon = event.weapon
        
        if weapon not in stats.weapon_stats:
            category = self.weapon_categories.get(weapon, WeaponCategory.ASSAULT_RIFLE)
            stats.weapon_stats[weapon] = WeaponStats(
                weapon_name=weapon,
                category=category,
            )
        
        ws = stats.weapon_stats[weapon]
        ws.shots_fired += max(1, event.damage // 15)  # Estimate shots from damage
        ws.shots_hit += 1 if event.damage > 0 else 0
        ws.damage += event.damage
        
        if event.headshot:
            ws.headshots += 1
        if event.is_kill:
            ws.kills += 1
        elif event.is_knock:
            ws.knocks += 1
        
        # Update effective range
        n = ws.kills + ws.knocks
        if n > 0:
            ws.effective_range = (
                ws.effective_range * (n - 1) + event.distance
            ) / n
        
        ws.update_accuracy()
        
        # Update favorite/best weapon
        if not stats.favorite_weapon or ws.kills > stats.weapon_stats.get(
            stats.favorite_weapon, WeaponStats(weapon_name="")).kills:
            stats.favorite_weapon = weapon
    
    def _update_kill_streak(self, player_id: str, is_kill: bool) -> None:
        """Update player kill streak"""
        stats = self.player_stats.get(player_id)
        if not stats:
            return
        
        if is_kill:
            stats.current_kill_streak += 1
            stats.best_kill_streak = max(
                stats.best_kill_streak, stats.current_kill_streak
            )
    
    def reset_kill_streak(self, player_id: str) -> None:
        """Reset player kill streak (on death)"""
        stats = self.player_stats.get(player_id)
        if stats:
            stats.current_kill_streak = 0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ROLE ASSESSMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _assess_player_role(self, stats: PlayerEngagementStats) -> None:
        """Assess player's combat role based on behavior"""
        
        if stats.total_engagements < 10:
            return  # Need more data
        
        scores = {}
        
        # Entry Fragger: Close range, high kills, high first blood
        entry_score = 0
        if stats.close_range_kills > stats.long_range_kills * 2:
            entry_score += 30
        if stats.avg_engagement_distance < 30:
            entry_score += 25
        if stats.first_blood_count > stats.total_engagements * 0.1:
            entry_score += 25
        if stats.best_kill_streak >= 3:
            entry_score += 20
        scores[PlayerRole.ENTRY_FRAGGER] = entry_score
        
        # Support: Revives, assists, mid-range
        support_score = 0
        if stats.total_assists > stats.total_kills * 0.5:
            support_score += 30
        if stats.avg_engagement_distance > 20 and stats.avg_engagement_distance < 80:
            support_score += 25
        if stats.avg_survival_in_fights > 30:
            support_score += 25
        scores[PlayerRole.SUPPORT] = support_score
        
        # Sniper: Long range, high accuracy, headshots
        sniper_score = 0
        if stats.long_range_kills > stats.close_range_kills:
            sniper_score += 35
        if stats.avg_engagement_distance > 80:
            sniper_score += 30
        if stats.headshot_percentage > 25:
            sniper_score += 20
        if stats.overall_accuracy > 30:
            sniper_score += 15
        scores[PlayerRole.SNIPER] = sniper_score
        
        # IGL: Survives long, balanced stats
        igl_score = 0
        if stats.avg_survival_in_fights > 45:
            igl_score += 35
        if stats.first_death_count < stats.total_engagements * 0.05:
            igl_score += 30
        if stats.total_damage > 0 and stats.total_kills / (stats.total_damage / 100) < 0.5:
            igl_score += 20
        scores[PlayerRole.IGL] = igl_score
        
        # Determine best role
        best_role = max(scores, key=scores.get)
        best_score = scores[best_role]
        
        stats.primary_role = best_role
        stats.role_confidence = min(1.0, best_score / 100)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SYNERGY ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def calculate_team_synergy(self, team_id: str) -> Dict[str, Any]:
        """Calculate team synergy metrics"""
        stats = self.team_stats.get(team_id)
        if not stats:
            return {"error": "No data"}
        
        # Analyze trade kills
        trade_rate = self._calculate_trade_kill_rate(team_id)
        refrag_rate = self._calculate_refrag_rate(team_id)
        
        stats.trade_kill_rate = trade_rate
        stats.refrag_rate = refrag_rate
        
        # Assess weapon distribution
        weapon_distribution = self._analyze_weapon_distribution(team_id)
        
        return {
            "trade_kill_rate": round(trade_rate * 100, 1),
            "refrag_rate": round(refrag_rate * 100, 1),
            "assist_rate": round(stats.assist_rate * 100, 1),
            "weapon_distribution": weapon_distribution,
            "role_distribution": self._analyze_role_distribution(team_id),
            "synergy_score": self._calculate_synergy_score(stats),
        }
    
    def _calculate_trade_kill_rate(self, team_id: str) -> float:
        """Calculate how often team trades kills"""
        # Trade: Kill enemy who just killed your teammate
        trades = 0
        total_deaths = 0
        
        engagements = list(self.recent_engagements)
        for i, event in enumerate(engagements):
            if event.is_kill and event.target_team_id == team_id:
                total_deaths += 1
                death_time = event.timestamp
                
                # Check if teammate killed the killer within 10 seconds
                for j in range(i + 1, min(len(engagements), i + 10)):
                    next_event = engagements[j]
                    if (next_event.is_kill and
                        next_event.player_team_id == team_id and
                        next_event.target_id == event.player_id and
                        next_event.timestamp - death_time < 10):
                        trades += 1
                        break
        
        return trades / max(1, total_deaths)
    
    def _calculate_refrag_rate(self, team_id: str) -> float:
        """Calculate refrag rate (killing enemy who killed teammate)"""
        refrags = 0
        teammate_deaths = 0
        
        engagements = list(self.recent_engagements)
        for i, event in enumerate(engagements):
            if event.is_kill and event.target_team_id == team_id:
                teammate_deaths += 1
                
                # Check if any teammate killed that enemy later
                for j in range(i + 1, len(engagements)):
                    next_event = engagements[j]
                    if (next_event.is_kill and
                        next_event.player_team_id == team_id and
                        next_event.target_id == event.player_id):
                        refrags += 1
                        break
        
        return refrags / max(1, teammate_deaths)
    
    def _analyze_weapon_distribution(self, team_id: str) -> Dict[str, int]:
        """Analyze team weapon distribution"""
        stats = self.team_stats.get(team_id)
        if not stats:
            return {}
        
        # Sort by frequency
        return dict(sorted(
            stats.team_weapon_profile.items(),
            key=lambda x: x[1],
            reverse=True,
        )[:10])
    
    def _analyze_role_distribution(self, team_id: str) -> Dict[str, List[str]]:
        """Analyze player role distribution in team"""
        roles = defaultdict(list)
        
        for player_id, pstats in self.player_stats.items():
            # Find players in this team (would need team membership data)
            if pstats.primary_role:
                roles[pstats.primary_role.value].append(pstats.player_name)
        
        return dict(roles)
    
    def _calculate_synergy_score(self, stats: TeamEngagementStats) -> float:
        """Calculate overall team synergy score (0-100)"""
        score = 50.0
        
        # Trade kills are good
        score += stats.trade_kill_rate * 25
        
        # Refrags are good
        score += stats.refrag_rate * 25
        
        # Assists show teamwork
        score += stats.assist_rate * 20
        
        # Good weapon distribution
        if len(stats.team_weapon_profile) >= 5:
            score += 5
        
        return min(100, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OPPONENT ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_opponent_analysis(self, team_id: str) -> Dict[str, Any]:
        """Get analysis against specific opponents"""
        stats = self.team_stats.get(team_id)
        if not stats:
            return {}
        
        analysis = {}
        for opp_id, opp_stats in stats.opponent_stats.items():
            encounters = opp_stats["encounters"]
            if encounters >= 2:
                analysis[opp_id] = {
                    "encounters": encounters,
                    "win_rate": round(opp_stats["wins"] / encounters * 100, 1),
                    "avg_kills": round(opp_stats["kills"] / encounters, 1),
                    "avg_damage": round(opp_stats["damage"] / encounters, 0),
                    "advantage": "favorable" if opp_stats["wins"] > opp_stats["losses"] else "unfavorable",
                }
        
        return analysis
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_player_analysis(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get complete player engagement analysis"""
        stats = self.player_stats.get(player_id)
        if not stats:
            return None
        
        return {
            "player_id": stats.player_id,
            "player_name": stats.player_name,
            "combat": {
                "total_engagements": stats.total_engagements,
                "win_rate": round(stats.engagements_won / max(1, stats.total_engagements) * 100, 1),
                "total_kills": stats.total_kills,
                "total_knocks": stats.total_knocks,
                "total_damage": stats.total_damage,
                "headshot_percentage": round(stats.headshot_percentage, 1),
                "accuracy": round(stats.overall_accuracy, 1),
            },
            "range": {
                "avg_distance": round(stats.avg_engagement_distance, 1),
                "preference": "close" if stats.close_range_kills > stats.long_range_kills else "long",
                "close_range_kills": stats.close_range_kills,
                "medium_range_kills": stats.medium_range_kills,
                "long_range_kills": stats.long_range_kills,
            },
            "role": {
                "primary": stats.primary_role.value,
                "confidence": round(stats.role_confidence, 2),
            },
            "weapons": {
                "favorite": stats.favorite_weapon,
                "top_weapons": dict(sorted(
                    {w: s.kills for w, s in stats.weapon_stats.items()}.items(),
                    key=lambda x: x[1], reverse=True
                )[:5]),
            },
            "streaks": {
                "current": stats.current_kill_streak,
                "best": stats.best_kill_streak,
            },
            "survival": {
                "avg_survival_seconds": round(stats.avg_survival_in_fights, 1),
                "first_bloods": stats.first_blood_count,
                "first_deaths": stats.first_death_count,
            },
        }
    
    def get_team_analysis(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get complete team engagement analysis"""
        stats = self.team_stats.get(team_id)
        if not stats:
            return None
        
        return {
            "team_id": stats.team_id,
            "overall": {
                "total_fights": stats.total_fights,
                "win_rate": round(stats.win_rate * 100, 1),
                "avg_kills_per_fight": round(stats.avg_kills_per_fight, 1),
                "avg_damage_per_fight": round(stats.avg_damage_per_fight, 0),
            },
            "style": {
                "preferred_range": stats.preferred_engagement_range,
                "close_range_tendency": round(stats.close_range_preference / max(1, stats.total_fights), 1),
                "long_range_tendency": round(stats.long_range_preference / max(1, stats.total_fights), 1),
            },
            "synergy": self.calculate_team_synergy(team_id),
            "weapons": self._analyze_weapon_distribution(team_id),
            "opponents": self.get_opponent_analysis(team_id),
            "utility": {
                "grenade_effectiveness": round(stats.grenade_effectiveness * 100, 1),
                "smoke_effectiveness": round(stats.smoke_effectiveness * 100, 1),
            },
        }
    
    def get_weapon_meta(self) -> Dict[str, Any]:
        """Get weapon meta analysis across all players"""
        weapon_totals = defaultdict(lambda: {"kills": 0, "damage": 0, "users": 0, "accuracy": 0})
        
        for pstats in self.player_stats.values():
            for weapon, wstats in pstats.weapon_stats.items():
                weapon_totals[weapon]["kills"] += wstats.kills
                weapon_totals[weapon]["damage"] += wstats.damage
                weapon_totals[weapon]["users"] += 1
                weapon_totals[weapon]["accuracy"] += wstats.accuracy
        
        # Calculate averages
        for weapon in weapon_totals:
            users = weapon_totals[weapon]["users"]
            if users > 0:
                weapon_totals[weapon]["avg_accuracy"] = weapon_totals[weapon]["accuracy"] / users
        
        # Sort by kills
        return dict(sorted(
            {w: {
                "kills": d["kills"],
                "damage": d["damage"],
                "users": d["users"],
                "avg_accuracy": round(d.get("avg_accuracy", 0), 1),
                "category": self.weapon_categories.get(w, WeaponCategory.ASSAULT_RIFLE).value,
            } for w, d in weapon_totals.items()}.items(),
            key=lambda x: x[1]["kills"],
            reverse=True,
        ))
    
    def get_stats(self) -> Dict[str, Any]:
        """Get analyzer statistics"""
        return {
            "players_analyzed": len(self.player_stats),
            "teams_analyzed": len(self.team_stats),
            "recent_engagements": len(self.recent_engagements),
        }
    
    def reset(self) -> None:
        """Reset analyzer"""
        self.player_stats.clear()
        self.team_stats.clear()
        self.recent_engagements.clear()
        self._log.info("Engagement analyzer reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("fight_ended")

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
