# backend/app/agents/analytics_agents/player_profiler.py
"""
AEGIS AI - Player Profiler
Classifies player playstyles, strengths, weaknesses,
and creates detailed player profiles for commentary and analysis.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog

logger = structlog.get_logger("aegis.agents.player_profiler")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Playstyle(str, Enum):
    """Player playstyle classifications"""
    AGGRESSIVE_FRAGGER = "aggressive_fragger"     # Entry fragger, high kills
    PASSIVE_FRAGGER = "passive_fragger"           # Careful but deadly
    SUPPORT_PLAYER = "support_player"              # Heals, revives, assists
    IGL_STRATEGIST = "igl_strategist"             # Strategic leader
    SNIPER_SPECIALIST = "sniper_specialist"        # Long-range expert
    ALL_ROUNDER = "all_rounder"                   # Balanced skills
    CLUTCH_SPECIALIST = "clutch_specialist"        # Thrives under pressure
    LURKER = "lurker"                             # Flank specialist
    ANCHOR = "anchor"                             # Holds positions
    SCOUT = "scout"                               # Information gatherer

class SkillLevel(str, Enum):
    """Player skill level assessment"""
    WORLD_CLASS = "world_class"
    EXPERT = "expert"
    ADVANCED = "advanced"
    INTERMEDIATE = "intermediate"
    DEVELOPING = "developing"
    ROOKIE = "rookie"

class PlayerTrait(str, Enum):
    """Individual player traits"""
    AGGRESSIVE = "aggressive"
    PATIENT = "patient"
    CREATIVE = "creative"
    METHODICAL = "methodical"
    RISK_TAKER = "risk_taker"
    SAFE_PLAYER = "safe_player"
    TEAM_PLAYER = "team_player"
    SOLO_CARRY = "solo_carry"
    COMMUNICATOR = "communicator"
    SHOT_CALLER = "shot_caller"

@dataclass
class PlayerProfile:
    """Complete player profile"""
    player_id: str
    player_name: str
    team_id: str
    team_name: str
    
    # Playstyle
    primary_style: Playstyle = Playstyle.ALL_ROUNDER
    secondary_styles: List[Playstyle] = field(default_factory=list)
    style_confidence: float = 0.0
    
    # Skill level
    skill_level: SkillLevel = SkillLevel.INTERMEDIATE
    skill_score: float = 50.0  # 0-100
    
    # Traits
    traits: List[PlayerTrait] = field(default_factory=list)
    
    # Strengths & weaknesses
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    
    # Weapon preferences
    favorite_weapons: List[str] = field(default_factory=list)
    weapon_proficiency: Dict[str, float] = field(default_factory=dict)
    
    # Stats summary
    avg_kills: float = 0.0
    avg_damage: float = 0.0
    avg_survival_time: float = 0.0
    headshot_percentage: float = 0.0
    clutch_rate: float = 0.0
    avg_engagement_distance: float = 0.0
    
    # Tendencies
    hot_drop_preference: float = 0.0  # 0-1
    rotation_speed: float = 0.0
    flank_tendency: float = 0.0
    solo_push_tendency: float = 0.0
    third_party_tendency: float = 0.0
    
    # History
    matches_analyzed: int = 0
    last_updated: float = field(default_factory=time.time)
    performance_trend: deque = field(default_factory=lambda: deque(maxlen=20))


class PlayerProfiler:
    """
    Analyzes and classifies player playstyles.
    
    Uses behavioral data to determine:
    - Primary playstyle (aggressive fragger, support, sniper, etc.)
    - Skill level assessment
    - Individual traits
    - Strengths and weaknesses
    - Weapon preferences
    - Behavioral tendencies
    
    Profiles improve with more matches analyzed.
    """
    
    def __init__(self):
        # Player profiles
        self.profiles: Dict[str, PlayerProfile] = {}
        
        # Playstyle classification thresholds
        self.style_thresholds = {
            Playstyle.AGGRESSIVE_FRAGGER: {
                "min_kills_per_match": 3.0,
                "min_damage_per_match": 800,
                "max_engagement_distance": 50,
                "min_aggression": 0.7,
            },
            Playstyle.SNIPER_SPECIALIST: {
                "min_engagement_distance": 80,
                "min_headshot_percentage": 25,
                "max_aggression": 0.5,
            },
            Playstyle.SUPPORT_PLAYER: {
                "min_revives_per_match": 1.5,
                "min_assists_per_match": 2.0,
                "max_kills_per_match": 2.0,
            },
            Playstyle.CLUTCH_SPECIALIST: {
                "min_clutch_rate": 0.2,
                "min_clutch_attempts": 3,
            },
        }
        
        # Trait correlations
        self.trait_correlations = {
            PlayerTrait.AGGRESSIVE: ["high_kills", "close_range", "hot_drop"],
            PlayerTrait.PATIENT: ["long_survival", "low_death_rate", "safe_drop"],
            PlayerTrait.CREATIVE: ["unique_rotations", "off_meta_weapons", "flank_tendency"],
            PlayerTrait.TEAM_PLAYER: ["high_assists", "revives", "team_damage_share"],
        }
        
        self._log = logger.bind(component="player_profiler")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROFILE CREATION & UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def analyze_player(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        team_name: str,
        match_stats: Dict[str, Any],
        historical_stats: Optional[Dict[str, Any]] = None,
    ) -> PlayerProfile:
        """
        Analyze a player and create/update their profile.
        
        Args:
            player_id: Player identifier
            player_name: Player name
            team_id: Team identifier
            team_name: Team name
            match_stats: Current match statistics
            historical_stats: Historical performance data
            
        Returns:
            Updated PlayerProfile
        """
        
        # Get or create profile
        if player_id not in self.profiles:
            self.profiles[player_id] = PlayerProfile(
                player_id=player_id,
                player_name=player_name,
                team_id=team_id,
                team_name=team_name,
            )
        
        profile = self.profiles[player_id]
        profile.player_name = player_name
        profile.team_id = team_id
        profile.team_name = team_name
        
        # Update stats
        self._update_stats(profile, match_stats)
        
        # Classify playstyle
        self._classify_playstyle(profile, match_stats, historical_stats)
        
        # Assess skill level
        self._assess_skill_level(profile)
        
        # Identify traits
        self._identify_traits(profile, match_stats)
        
        # Find strengths and weaknesses
        self._find_strengths_weaknesses(profile)
        
        # Update weapon preferences
        self._update_weapons(profile, match_stats)
        
        profile.matches_analyzed += 1
        profile.last_updated = time.time()
        
        self._log.info(
            f"Player profiled: {player_name}",
            style=profile.primary_style.value,
            skill=profile.skill_level.value,
            matches=profile.matches_analyzed,
        )
        
        return profile
    
    def _update_stats(self, profile: PlayerProfile, stats: Dict[str, Any]) -> None:
        """Update profile statistics"""
        
        n = max(1, profile.matches_analyzed)
        
        # Update averages
        profile.avg_kills = (profile.avg_kills * n + stats.get("kills", 0)) / (n + 1)
        profile.avg_damage = (profile.avg_damage * n + stats.get("damage", 0)) / (n + 1)
        profile.avg_survival_time = (
            profile.avg_survival_time * n + stats.get("survival_time", 0)
        ) / (n + 1)
        
        # Update percentages
        headshots = stats.get("headshots", 0)
        total_kills = stats.get("kills", 1)
        profile.headshot_percentage = (
            profile.headshot_percentage * n + (headshots / total_kills * 100)
        ) / (n + 1)
        
        # Update tendencies
        profile.hot_drop_preference = (
            profile.hot_drop_preference * n + stats.get("hot_drop", 0)
        ) / (n + 1)
        profile.flank_tendency = (
            profile.flank_tendency * n + stats.get("flank_kills", 0) / max(1, stats.get("kills", 1))
        ) / (n + 1)
        profile.solo_push_tendency = (
            profile.solo_push_tendency * n + stats.get("solo_kills", 0) / max(1, stats.get("kills", 1))
        ) / (n + 1)
        
        # Update engagement distance
        if stats.get("engagement_distance", 0) > 0:
            profile.avg_engagement_distance = (
                profile.avg_engagement_distance * n + stats.get("engagement_distance", 0)
            ) / (n + 1)
        
        # Track performance trend
        profile.performance_trend.append({
            "kills": stats.get("kills", 0),
            "damage": stats.get("damage", 0),
            "timestamp": time.time(),
        })
    
    def _classify_playstyle(
        self,
        profile: PlayerProfile,
        stats: Dict[str, Any],
        historical: Optional[Dict[str, Any]],
    ) -> None:
        """Classify player's playstyle"""
        
        scores = {}
        
        # Aggressive Fragger
        agg_score = 0
        if profile.avg_kills >= self.style_thresholds[Playstyle.AGGRESSIVE_FRAGGER]["min_kills_per_match"]:
            agg_score += 30
        if profile.avg_damage >= self.style_thresholds[Playstyle.AGGRESSIVE_FRAGGER]["min_damage_per_match"]:
            agg_score += 25
        if profile.avg_engagement_distance <= self.style_thresholds[Playstyle.AGGRESSIVE_FRAGGER]["max_engagement_distance"]:
            agg_score += 25
        if stats.get("aggression", 0.5) >= self.style_thresholds[Playstyle.AGGRESSIVE_FRAGGER]["min_aggression"]:
            agg_score += 20
        scores[Playstyle.AGGRESSIVE_FRAGGER] = agg_score
        
        # Sniper Specialist
        sniper_score = 0
        if profile.avg_engagement_distance >= self.style_thresholds[Playstyle.SNIPER_SPECIALIST]["min_engagement_distance"]:
            sniper_score += 35
        if profile.headshot_percentage >= self.style_thresholds[Playstyle.SNIPER_SPECIALIST]["min_headshot_percentage"]:
            sniper_score += 35
        if stats.get("aggression", 0.5) <= self.style_thresholds[Playstyle.SNIPER_SPECIALIST]["max_aggression"]:
            sniper_score += 30
        scores[Playstyle.SNIPER_SPECIALIST] = sniper_score
        
        # Support Player
        support_score = 0
        revives = stats.get("revives", 0)
        assists = stats.get("assists", 0)
        if revives >= self.style_thresholds[Playstyle.SUPPORT_PLAYER]["min_revives_per_match"]:
            support_score += 35
        if assists >= self.style_thresholds[Playstyle.SUPPORT_PLAYER]["min_assists_per_match"]:
            support_score += 35
        if profile.avg_kills <= self.style_thresholds[Playstyle.SUPPORT_PLAYER]["max_kills_per_match"]:
            support_score += 30
        scores[Playstyle.SUPPORT_PLAYER] = support_score
        
        # Clutch Specialist
        clutch_score = 0
        clutch_attempts = stats.get("clutch_attempts", 0)
        clutch_wins = stats.get("clutch_wins", 0)
        clutch_rate = clutch_wins / max(1, clutch_attempts)
        
        if clutch_rate >= self.style_thresholds[Playstyle.CLUTCH_SPECIALIST]["min_clutch_rate"]:
            clutch_score += 40
        if clutch_attempts >= self.style_thresholds[Playstyle.CLUTCH_SPECIALIST]["min_clutch_attempts"]:
            clutch_score += 30
        if profile.avg_survival_time > 900:  # 15 minutes
            clutch_score += 30
        scores[Playstyle.CLUTCH_SPECIALIST] = clutch_score
        
        # IGL Strategist
        igl_score = 0
        if stats.get("is_igl", False):
            igl_score += 40
        if profile.avg_survival_time > 1200:  # 20 minutes
            igl_score += 30
        if assists >= 3:
            igl_score += 30
        scores[Playstyle.IGL_STRATEGIST] = igl_score
        
        # All Rounder
        all_score = 50  # Base score for balanced players
        scores[Playstyle.ALL_ROUNDER] = all_score
        
        # Determine primary style
        best_style = max(scores, key=scores.get)
        best_score = scores[best_style]
        
        profile.primary_style = best_style
        profile.style_confidence = min(1.0, best_score / 100)
        
        # Secondary styles (close contenders)
        profile.secondary_styles = [
            style for style, score in scores.items()
            if style != best_style and score > best_score * 0.8
        ]
    
    def _assess_skill_level(self, profile: PlayerProfile) -> None:
        """Assess player's skill level"""
        
        score = 0
        
        # Kill-based assessment
        if profile.avg_kills >= 5:
            score += 25
        elif profile.avg_kills >= 3:
            score += 15
        elif profile.avg_kills >= 1:
            score += 5
        
        # Damage-based
        if profile.avg_damage >= 1500:
            score += 25
        elif profile.avg_damage >= 800:
            score += 15
        elif profile.avg_damage >= 400:
            score += 5
        
        # Survival-based
        if profile.avg_survival_time >= 1200:
            score += 20
        elif profile.avg_survival_time >= 600:
            score += 10
        
        # Headshot accuracy
        if profile.headshot_percentage >= 30:
            score += 15
        elif profile.headshot_percentage >= 20:
            score += 10
        
        # Clutch ability
        if profile.clutch_rate >= 0.3:
            score += 15
        elif profile.clutch_rate >= 0.15:
            score += 10
        
        profile.skill_score = score
        
        # Determine level
        if score >= 85:
            profile.skill_level = SkillLevel.WORLD_CLASS
        elif score >= 70:
            profile.skill_level = SkillLevel.EXPERT
        elif score >= 55:
            profile.skill_level = SkillLevel.ADVANCED
        elif score >= 35:
            profile.skill_level = SkillLevel.INTERMEDIATE
        elif score >= 20:
            profile.skill_level = SkillLevel.DEVELOPING
        else:
            profile.skill_level = SkillLevel.ROOKIE
    
    def _identify_traits(self, profile: PlayerProfile, stats: Dict[str, Any]) -> None:
        """Identify player traits"""
        
        traits = []
        
        # Aggressive
        if profile.avg_kills >= 3 and profile.avg_engagement_distance < 50:
            traits.append(PlayerTrait.AGGRESSIVE)
        
        # Patient
        if profile.avg_survival_time > 1200 and stats.get("deaths", 1) < 2:
            traits.append(PlayerTrait.PATIENT)
        
        # Creative
        if stats.get("unique_weapons", 0) >= 3 or profile.flank_tendency > 0.3:
            traits.append(PlayerTrait.CREATIVE)
        
        # Team player
        if stats.get("assists", 0) >= 2 and stats.get("revives", 0) >= 1:
            traits.append(PlayerTrait.TEAM_PLAYER)
        
        # Risk taker
        if profile.hot_drop_preference > 0.6:
            traits.append(PlayerTrait.RISK_TAKER)
        
        # Safe player
        if profile.avg_survival_time > 1500:
            traits.append(PlayerTrait.SAFE_PLAYER)
        
        # Solo carry
        damage_share = stats.get("team_damage_share", 0)
        if damage_share > 0.4:
            traits.append(PlayerTrait.SOLO_CARRY)
        
        profile.traits = traits
    
    def _find_strengths_weaknesses(self, profile: PlayerProfile) -> None:
        """Identify player strengths and weaknesses"""
        
        strengths = []
        weaknesses = []
        
        # Combat strength
        if profile.avg_kills >= 3:
            strengths.append("Excellent fragging ability")
        elif profile.avg_kills < 1:
            weaknesses.append("Below average kill count")
        
        # Damage output
        if profile.avg_damage >= 1000:
            strengths.append("High damage output")
        elif profile.avg_damage < 300:
            weaknesses.append("Low damage contribution")
        
        # Survival
        if profile.avg_survival_time >= 1200:
            strengths.append("Exceptional survival skills")
        elif profile.avg_survival_time < 300:
            weaknesses.append("Poor survival rate")
        
        # Accuracy
        if profile.headshot_percentage >= 25:
            strengths.append("Deadly headshot accuracy")
        elif profile.headshot_percentage < 10:
            weaknesses.append("Needs aim improvement")
        
        # Clutch
        if profile.clutch_rate >= 0.25:
            strengths.append("Clutch player under pressure")
        
        # Team play
        if PlayerTrait.TEAM_PLAYER in profile.traits:
            strengths.append("Strong team player")
        
        profile.strengths = strengths
        profile.weaknesses = weaknesses
    
    def _update_weapons(self, profile: PlayerProfile, stats: Dict[str, Any]) -> None:
        """Update weapon preferences"""
        
        weapons = stats.get("weapons_used", {})
        
        for weapon, kills in weapons.items():
            if weapon not in profile.weapon_proficiency:
                profile.weapon_proficiency[weapon] = 0
            profile.weapon_proficiency[weapon] += kills
        
        # Sort by proficiency
        sorted_weapons = sorted(
            profile.weapon_proficiency.items(),
            key=lambda x: x[1],
            reverse=True,
        )
        
        profile.favorite_weapons = [w for w, _ in sorted_weapons[:5]]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_profile(self, player_id: str) -> Optional[PlayerProfile]:
        """Get player profile"""
        return self.profiles.get(player_id)
    
    def get_profiles_by_style(self, style: Playstyle) -> List[PlayerProfile]:
        """Get all players with a specific playstyle"""
        return [
            p for p in self.profiles.values()
            if p.primary_style == style
        ]
    
    def get_top_players_by_skill(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get top players by skill score"""
        sorted_players = sorted(
            self.profiles.values(),
            key=lambda p: p.skill_score,
            reverse=True,
        )[:limit]
        
        return [
            {
                "name": p.player_name,
                "team": p.team_name,
                "style": p.primary_style.value,
                "skill": p.skill_level.value,
                "score": p.skill_score,
                "avg_kills": round(p.avg_kills, 1),
                "avg_damage": round(p.avg_damage, 0),
                "traits": [t.value for t in p.traits],
            }
            for p in sorted_players
        ]
    
    def get_playstyle_distribution(self) -> Dict[str, int]:
        """Get distribution of playstyles"""
        distribution = defaultdict(int)
        for profile in self.profiles.values():
            distribution[profile.primary_style.value] += 1
        return dict(distribution)
    
    def get_profile_summary(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get profile summary for API"""
        profile = self.profiles.get(player_id)
        if not profile:
            return None
        
        return {
            "player_name": profile.player_name,
            "team_name": profile.team_name,
            "playstyle": {
                "primary": profile.primary_style.value,
                "secondary": [s.value for s in profile.secondary_styles],
                "confidence": round(profile.style_confidence, 2),
            },
            "skill": {
                "level": profile.skill_level.value,
                "score": round(profile.skill_score, 1),
            },
            "traits": [t.value for t in profile.traits],
            "strengths": profile.strengths,
            "weaknesses": profile.weaknesses,
            "weapons": {
                "favorites": profile.favorite_weapons,
            },
            "stats": {
                "avg_kills": round(profile.avg_kills, 1),
                "avg_damage": round(profile.avg_damage, 0),
                "avg_survival": round(profile.avg_survival_time, 0),
                "headshot_pct": round(profile.headshot_percentage, 1),
                "clutch_rate": round(profile.clutch_rate * 100, 1),
            },
            "matches_analyzed": profile.matches_analyzed,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get profiler statistics"""
        return {
            "total_profiled": len(self.profiles),
            "playstyle_distribution": self.get_playstyle_distribution(),
            "top_players": self.get_top_players_by_skill(5),
        }
    
    def reset(self) -> None:
        """Reset profiler"""
        self.profiles.clear()
        self._log.info("Player profiler reset")