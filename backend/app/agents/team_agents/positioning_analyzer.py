# backend/app/agents/team_agents/positioning_analyzer.py
"""
AEGIS AI - Positioning Analyzer
Analyzes team positioning, spacing, cover usage,
high-ground advantage, and tactical positioning quality.
"""

import math
import time
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from collections import deque
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.agents.positioning_analyzer")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PositionQuality(str, Enum):
    """Quality assessment of current position"""
    EXCELLENT = "excellent"
    GOOD = "good"
    AVERAGE = "average"
    POOR = "poor"
    DANGEROUS = "dangerous"
    UNKNOWN = "unknown"

class CoverType(str, Enum):
    """Types of cover"""
    HARD_COVER = "hard_cover"       # Walls, rocks, buildings
    SOFT_COVER = "soft_cover"       # Trees, fences, vehicles
    NO_COVER = "no_cover"           # Open field
    SMOKE_COVER = "smoke_cover"     # Behind smoke
    HIGH_GROUND = "high_ground"     # Elevated position
    LOW_GROUND = "low_ground"       # Lower than enemy
    COMPOUND = "compound"           # Inside building/compound
    UNKNOWN = "unknown"

class FormationType(str, Enum):
    """Team formation types"""
    STACKED = "stacked"         # All very close
    LINE = "line"              # Side by side
    DIAMOND = "diamond"        # Diamond formation
    SCATTERED = "scattered"    # Spread out
    PAIRS = "pairs"            # Two pairs
    SOLO_WIDE = "solo_wide"    # One alive, moving
    UNKNOWN = "unknown"

class EngagementType(str, Enum):
    """Type of engagement based on positioning"""
    CLOSE_QUARTERS = "close_quarters"    # < 25m
    MEDIUM_RANGE = "medium_range"        # 25-100m
    LONG_RANGE = "long_range"            # 100-300m
    EXTREME_RANGE = "extreme_range"      # > 300m
    UNKNOWN = "unknown"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@dataclass
class PlayerPosition:
    """Detailed player position analysis"""
    player_id: str
    player_name: str
    x: float
    y: float
    z: float = 0.0
    yaw: float = 0.0  # Looking direction
    
    # Cover analysis
    cover_type: CoverType = CoverType.UNKNOWN
    cover_quality: float = 0.0  # 0-100
    has_head_glitch: bool = False
    is_exposed: bool = True
    exposure_percentage: float = 100.0  # How exposed (0-100)
    
    # Relative positioning
    distance_to_team_center: float = 0.0
    distance_to_nearest_teammate: float = 9999.0
    is_isolated: bool = False
    
    # Threat assessment
    visible_to_enemy: bool = False
    enemy_has_angle: bool = False
    flank_exposed: bool = False
    
    # Advantage
    has_high_ground: bool = False
    height_advantage: float = 0.0  # Meters above enemy
    line_of_sight_blocked: bool = False
    
    timestamp: float = field(default_factory=time.time)

@dataclass
class TeamPositioning:
    """Complete team positioning analysis"""
    team_id: str
    timestamp: float = field(default_factory=time.time)
    
    # Positions
    player_positions: Dict[str, PlayerPosition] = field(default_factory=dict)
    
    # Team-wide metrics
    formation: FormationType = FormationType.UNKNOWN
    spread_radius: float = 0.0  # Max distance between any two players
    average_spacing: float = 0.0  # Average distance between teammates
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    
    # Quality
    overall_quality: PositionQuality = PositionQuality.UNKNOWN
    quality_score: float = 0.0  # 0-100
    defensive_score: float = 0.0
    offensive_score: float = 0.0
    
    # Cover
    players_in_cover: int = 0
    players_exposed: int = 0
    average_cover_quality: float = 0.0
    
    # Zone
    in_zone: bool = True
    distance_to_zone_edge: float = 0.0
    zone_position_quality: float = 0.0  # How good is position relative to zone
    
    # Threats
    enemy_positions: List[Tuple[float, float, float]] = field(default_factory=list)
    nearest_enemy_distance: float = 99999.0
    engagement_type: EngagementType = EngagementType.UNKNOWN
    flank_risk: float = 0.0  # 0-100
    pinch_risk: float = 0.0  # 0-100
    
    # Compound
    in_compound: bool = False
    compound_name: Optional[str] = None
    compound_quality: float = 0.0
    
    # Rotation
    rotation_path_quality: float = 0.0
    next_zone_prediction: Optional[Tuple[float, float]] = None

@dataclass
class PositionHistory:
    """Historical position data for trend analysis"""
    positions: deque = field(default_factory=lambda: deque(maxlen=300))
    quality_history: deque = field(default_factory=lambda: deque(maxlen=100))
    formation_changes: int = 0
    time_in_good_position: float = 0.0
    time_in_poor_position: float = 0.0


class PositioningAnalyzer:
    """
    Analyzes team positioning in real-time.
    
    Analyzes:
    - Individual player positioning
    - Cover usage and quality
    - Team formation and spacing
    - High-ground advantage
    - Exposure to enemy fire
    - Flank and pinch risks
    - Compound/zone positioning
    - Rotation path quality
    - Engagement distance optimization
    """
    
    def __init__(self, team_id: str):
        self.team_id = team_id
        
        # Current positioning
        self.positioning = TeamPositioning(team_id=team_id)
        
        # History
        self.history = PositionHistory()
        
        # Map knowledge (simplified - would be loaded from map data)
        self.compound_locations = [
            # Erangel compounds (x, y, radius, name, quality)
            (4500, 5500, 200, "Pochinki", 90),
            (6000, 3000, 250, "Georgopol", 85),
            (3000, 7000, 300, "Military Base", 95),
            (5500, 4500, 150, "School", 80),
            (4000, 4000, 120, "Rozhok", 70),
            (2500, 5000, 150, "Gatka", 65),
            (6500, 5500, 100, "Yasnaya", 75),
            (1500, 3000, 100, "Primorsk", 60),
            (7000, 2000, 80, "Severny", 55),
            (5000, 6500, 100, "Mylta", 65),
        ]
        
        # High ground areas (x, y, radius, height)
        self.high_ground_areas = [
            (5500, 4500, 300, 50),   # Stalber
            (3000, 6500, 200, 40),   # Military hill
            (4500, 3500, 150, 30),   # Mountain
        ]
        
        # Configuration
        self.ideal_spacing = {
            "close_quarters": 5.0,    # 5 meters apart in CQC
            "medium_range": 15.0,     # 15 meters for medium range
            "long_range": 30.0,       # 30 meters for long range
            "default": 10.0,          # Default spacing
        }
        
        self._log = logger.bind(team_id=team_id)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # POSITION UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_player_position(
        self,
        player_id: str,
        player_name: str,
        x: float,
        y: float,
        z: float = 0.0,
        yaw: float = 0.0,
        is_alive: bool = True,
    ) -> PlayerPosition:
        """Update a player's position and analyze it"""
        
        # Create position analysis
        pos = PlayerPosition(
            player_id=player_id,
            player_name=player_name,
            x=x, y=y, z=z, yaw=yaw,
        )
        
        # Analyze cover
        self._analyze_cover(pos)
        
        # Analyze exposure
        self._analyze_exposure(pos)
        
        # Check high ground
        self._analyze_high_ground(pos)
        
        # Store in positioning
        if is_alive:
            self.positioning.player_positions[player_id] = pos
        else:
            self.positioning.player_positions.pop(player_id, None)
        
        # Update team-wide analysis
        self._update_team_analysis()
        
        return pos
    
    def update_enemy_positions(
        self,
        enemies: List[Tuple[float, float, float]],
    ) -> None:
        """Update known enemy positions"""
        self.positioning.enemy_positions = enemies
        
        if enemies:
            # Calculate nearest enemy
            if self.positioning.player_positions:
                min_dist = float("inf")
                for player_pos in self.positioning.player_positions.values():
                    for ex, ey, ez in enemies:
                        dist = math.sqrt(
                            (player_pos.x - ex) ** 2 + 
                            (player_pos.y - ey) ** 2
                        )
                        min_dist = min(min_dist, dist)
                
                self.positioning.nearest_enemy_distance = min_dist
                
                # Determine engagement type
                self._determine_engagement_type(min_dist)
            
            # Analyze threats
            self._analyze_threats()
    
    def update_zone(
        self,
        zone_x: float,
        zone_y: float,
        zone_radius: float,
        next_zone_x: Optional[float] = None,
        next_zone_y: Optional[float] = None,
    ) -> None:
        """Update zone information"""
        if self.positioning.player_positions:
            # Calculate center
            cx = sum(p.x for p in self.positioning.player_positions.values()) / len(self.positioning.player_positions)
            cy = sum(p.y for p in self.positioning.player_positions.values()) / len(self.positioning.player_positions)
            
            dist_to_center = math.sqrt((cx - zone_x) ** 2 + (cy - zone_y) ** 2)
            
            self.positioning.in_zone = dist_to_center <= zone_radius
            self.positioning.distance_to_zone_edge = max(0, dist_to_center - zone_radius)
        
        if next_zone_x and next_zone_y:
            self.positioning.next_zone_prediction = (next_zone_x, next_zone_y)
        
        self._analyze_zone_positioning()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COVER ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _analyze_cover(self, pos: PlayerPosition) -> None:
        """Analyze what cover a player has"""
        
        # Check if in compound
        in_compound, compound_name, compound_quality = self._check_in_compound(pos.x, pos.y)
        
        if in_compound:
            pos.cover_type = CoverType.COMPOUND
            pos.cover_quality = compound_quality
            pos.is_exposed = compound_quality < 50
            pos.exposure_percentage = max(0, 100 - compound_quality)
            self.positioning.in_compound = True
            self.positioning.compound_name = compound_name
            self.positioning.compound_quality = compound_quality
            return
        
        # Check if on high ground
        on_high_ground, height = self._check_high_ground(pos.x, pos.y, pos.z)
        if on_high_ground:
            pos.cover_type = CoverType.HIGH_GROUND
            pos.cover_quality = 70
            pos.has_high_ground = True
            pos.height_advantage = height
            pos.is_exposed = True
            pos.exposure_percentage = 60
            return
        
        # Default: assess based on z-level (simplified)
        if pos.z > 10:  # On a building
            pos.cover_type = CoverType.HARD_COVER
            pos.cover_quality = 60
            pos.is_exposed = False
            pos.exposure_percentage = 40
        elif pos.z > 2:  # Slight elevation
            pos.cover_type = CoverType.SOFT_COVER
            pos.cover_quality = 40
            pos.is_exposed = True
            pos.exposure_percentage = 70
        else:
            pos.cover_type = CoverType.NO_COVER
            pos.cover_quality = 10
            pos.is_exposed = True
            pos.exposure_percentage = 90
    
    def _check_in_compound(
        self, x: float, y: float
    ) -> Tuple[bool, Optional[str], float]:
        """Check if position is inside a named compound"""
        for cx, cy, radius, name, quality in self.compound_locations:
            distance = math.sqrt((x - cx) ** 2 + (y - cy) ** 2)
            if distance < radius:
                # Closer to center = better cover
                center_factor = 1 - (distance / radius)
                adjusted_quality = quality * (0.5 + 0.5 * center_factor)
                return True, name, adjusted_quality
        
        return False, None, 0.0
    
    def _check_high_ground(
        self, x: float, y: float, z: float
    ) -> Tuple[bool, float]:
        """Check if position is on high ground"""
        for hx, hy, radius, height in self.high_ground_areas:
            distance = math.sqrt((x - hx) ** 2 + (y - hy) ** 2)
            if distance < radius and z > 5:
                return True, height
        
        return False, 0.0
    
    def _analyze_high_ground(self, pos: PlayerPosition) -> None:
        """Analyze high ground advantage"""
        for hx, hy, radius, height in self.high_ground_areas:
            distance = math.sqrt((pos.x - hx) ** 2 + (pos.y - hy) ** 2)
            if distance < radius:
                pos.has_high_ground = True
                pos.height_advantage = height
                pos.cover_quality = min(100, pos.cover_quality + 20)
                break
    
    def _analyze_exposure(self, pos: PlayerPosition) -> None:
        """Analyze how exposed a player is"""
        # Simplified exposure analysis
        # In production, would use raycasting or map data
        
        exposure = 0
        
        # No cover = fully exposed
        if pos.cover_type == CoverType.NO_COVER:
            exposure = 90
        elif pos.cover_type == CoverType.SOFT_COVER:
            exposure = 60
        elif pos.cover_type == CoverType.HARD_COVER:
            exposure = 30
        elif pos.cover_type == CoverType.COMPOUND:
            exposure = 20
        elif pos.cover_type == CoverType.HIGH_GROUND:
            exposure = 40
        
        # Check if enemies have angle
        if pos.enemy_has_angle:
            exposure = min(100, exposure + 30)
        
        pos.exposure_percentage = exposure
        pos.is_exposed = exposure > 50
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEAM ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_team_analysis(self) -> None:
        """Update team-wide positioning analysis"""
        positions = list(self.positioning.player_positions.values())
        
        if not positions:
            return
        
        # Calculate team center
        self.positioning.center_x = sum(p.x for p in positions) / len(positions)
        self.positioning.center_y = sum(p.y for p in positions) / len(positions)
        self.positioning.center_z = sum(p.z for p in positions) / len(positions)
        
        # Calculate distances between all pairs
        if len(positions) >= 2:
            distances = []
            for i, p1 in enumerate(positions):
                min_dist = float("inf")
                for j, p2 in enumerate(positions):
                    if i != j:
                        dist = math.sqrt(
                            (p1.x - p2.x) ** 2 + 
                            (p1.y - p2.y) ** 2
                        )
                        distances.append(dist)
                        min_dist = min(min_dist, dist)
                
                p1.distance_to_nearest_teammate = min_dist
                p1.is_isolated = min_dist > 50  # More than 50m from nearest teammate
            
            self.positioning.spread_radius = max(distances)
            self.positioning.average_spacing = sum(distances) / len(distances)
        else:
            self.positioning.spread_radius = 0.0
            self.positioning.average_spacing = 0.0
        
        # Determine formation
        self._determine_formation()
        
        # Calculate cover stats
        self.positioning.players_in_cover = sum(
            1 for p in positions if not p.is_exposed
        )
        self.positioning.players_exposed = sum(
            1 for p in positions if p.is_exposed
        )
        self.positioning.average_cover_quality = (
            sum(p.cover_quality for p in positions) / len(positions)
        )
        
        # Calculate quality scores
        self._calculate_quality_scores()
        
        # Update history
        self.history.positions.append({
            "timestamp": time.time(),
            "center": (self.positioning.center_x, self.positioning.center_y),
            "spread": self.positioning.spread_radius,
            "formation": self.positioning.formation.value,
            "quality": self.positioning.overall_quality.value,
        })
        self.history.quality_history.append(self.positioning.quality_score)
    
    def _determine_formation(self) -> None:
        """Determine current team formation"""
        positions = list(self.positioning.player_positions.values())
        n = len(positions)
        
        if n == 0:
            self.positioning.formation = FormationType.UNKNOWN
            return
        elif n == 1:
            self.positioning.formation = FormationType.SOLO_WIDE
            return
        elif n == 2:
            dist = math.sqrt(
                (positions[0].x - positions[1].x) ** 2 +
                (positions[0].y - positions[1].y) ** 2
            )
            if dist < 10:
                self.positioning.formation = FormationType.STACKED
            else:
                self.positioning.formation = FormationType.PAIRS
            return
        
        # Calculate relative positions from center
        cx, cy = self.positioning.center_x, self.positioning.center_y
        
        rel_positions = []
        for p in positions:
            rel_x = p.x - cx
            rel_y = p.y - cy
            angle = math.degrees(math.atan2(rel_y, rel_x))
            distance = math.sqrt(rel_x ** 2 + rel_y ** 2)
            rel_positions.append((angle % 360, distance))
        
        rel_positions.sort(key=lambda x: x[0])
        
        # Analyze formation
        max_dist = max(d for _, d in rel_positions)
        min_dist = min(d for _, d in rel_positions)
        
        if max_dist < 10:
            self.positioning.formation = FormationType.STACKED
        elif max_dist > 100:
            self.positioning.formation = FormationType.SCATTERED
        elif n == 4:
            # Check for diamond formation
            angles = [a for a, _ in rel_positions]
            angle_diffs = []
            for i in range(len(angles)):
                diff = angles[(i + 1) % len(angles)] - angles[i]
                if diff < 0:
                    diff += 360
                angle_diffs.append(diff)
            
            avg_diff = sum(angle_diffs) / len(angle_diffs)
            if 70 < avg_diff < 110:
                self.positioning.formation = FormationType.DIAMOND
            elif max(angle_diffs) - min(angle_diffs) < 45:
                self.positioning.formation = FormationType.LINE
            else:
                self.positioning.formation = FormationType.SCATTERED
        else:
            self.positioning.formation = FormationType.SCATTERED
    
    def _calculate_quality_scores(self) -> None:
        """Calculate positioning quality scores"""
        score = 50.0  # Base score
        
        positions = list(self.positioning.player_positions.values())
        if not positions:
            self.positioning.quality_score = 0
            self.positioning.overall_quality = PositionQuality.UNKNOWN
            return
        
        # Cover quality bonus
        score += self.positioning.average_cover_quality * 0.2
        
        # Spacing bonus (optimal spacing depends on engagement type)
        ideal_spacing = self.ideal_spacing.get(
            self.positioning.engagement_type.value if self.positioning.engagement_type != EngagementType.UNKNOWN else "default",
            self.ideal_spacing["default"]
        )
        
        spacing_score = 0
        for p in positions:
            diff = abs(p.distance_to_nearest_teammate - ideal_spacing)
            if diff < 5:
                spacing_score += 15
            elif diff < 15:
                spacing_score += 10
            elif diff < 30:
                spacing_score += 5
            else:
                spacing_score -= 5
        
        spacing_score /= len(positions)
        score += spacing_score
        
        # Formation bonus
        formation_bonus = {
            FormationType.DIAMOND: 15,
            FormationType.LINE: 10,
            FormationType.PAIRS: 8,
            FormationType.STACKED: -5,  # Too close
            FormationType.SCATTERED: -10,  # Too spread
            FormationType.SOLO_WIDE: 0,
        }
        score += formation_bonus.get(self.positioning.formation, 0)
        
        # High ground bonus
        high_ground_players = sum(1 for p in positions if p.has_high_ground)
        score += high_ground_players * 10
        
        # Exposure penalty
        score -= self.positioning.players_exposed * 8
        
        # Zone position bonus
        if self.positioning.in_zone:
            score += 10
        
        # Compound bonus
        if self.positioning.in_compound:
            score += self.positioning.compound_quality * 0.15
        
        # Enemy proximity penalty
        if self.positioning.nearest_enemy_distance < 25:
            score -= 10
        elif self.positioning.nearest_enemy_distance < 50:
            score -= 5
        
        # Flank risk penalty
        score -= self.positioning.flank_risk * 0.2
        score -= self.positioning.pinch_risk * 0.15
        
        # Clamp score
        self.positioning.quality_score = max(0, min(100, score))
        
        # Determine quality level
        if self.positioning.quality_score >= 80:
            self.positioning.overall_quality = PositionQuality.EXCELLENT
        elif self.positioning.quality_score >= 60:
            self.positioning.overall_quality = PositionQuality.GOOD
        elif self.positioning.quality_score >= 40:
            self.positioning.overall_quality = PositionQuality.AVERAGE
        elif self.positioning.quality_score >= 20:
            self.positioning.overall_quality = PositionQuality.POOR
        else:
            self.positioning.overall_quality = PositionQuality.DANGEROUS
        
        # Calculate sub-scores
        self.positioning.defensive_score = self._calculate_defensive_score()
        self.positioning.offensive_score = self._calculate_offensive_score()
    
    def _calculate_defensive_score(self) -> float:
        """Calculate defensive positioning score"""
        score = 50.0
        
        # Cover is good for defense
        score += self.positioning.average_cover_quality * 0.3
        
        # Being in zone is defensive
        if self.positioning.in_zone:
            score += 15
            # Being near zone edge is good for holding
            if self.positioning.distance_to_zone_edge < 50:
                score += 10
        
        # Compound is good for defense
        if self.positioning.in_compound:
            score += self.positioning.compound_quality * 0.2
        
        # Low exposure is good
        score -= self.positioning.players_exposed * 10
        
        # Being spread too thin is bad
        if self.positioning.spread_radius > 100:
            score -= 15
        
        return max(0, min(100, score))
    
    def _calculate_offensive_score(self) -> float:
        """Calculate offensive positioning score"""
        score = 50.0
        
        # High ground is great for offense
        positions = list(self.positioning.player_positions.values())
        high_ground_count = sum(1 for p in positions if p.has_high_ground)
        score += high_ground_count * 15
        
        # Good spacing for pushes
        if 10 <= self.positioning.average_spacing <= 30:
            score += 10
        
        # Close to enemy = aggressive position
        if self.positioning.nearest_enemy_distance < 30:
            score += 20
        elif self.positioning.nearest_enemy_distance < 100:
            score += 10
        
        # Flanking opportunity
        if self.positioning.flank_risk < 30 and self.positioning.nearest_enemy_distance < 50:
            score += 15
        
        return max(0, min(100, score))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # THREAT ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _analyze_threats(self) -> None:
        """Analyze threats from enemy positions"""
        positions = list(self.positioning.player_positions.values())
        enemies = self.positioning.enemy_positions
        
        if not positions or not enemies:
            self.positioning.flank_risk = 0
            self.positioning.pinch_risk = 0
            return
        
        # Calculate flank risk
        # Check if enemies are on multiple sides
        cx, cy = self.positioning.center_x, self.positioning.center_y
        
        enemy_angles = []
        for ex, ey, _ in enemies:
            angle = math.degrees(math.atan2(ey - cy, ex - cx)) % 360
            enemy_angles.append(angle)
        
        enemy_angles.sort()
        
        if len(enemy_angles) >= 2:
            # Check angle spread
            max_angle_diff = 0
            for i in range(len(enemy_angles)):
                diff = enemy_angles[(i + 1) % len(enemy_angles)] - enemy_angles[i]
                if diff < 0:
                    diff += 360
                max_angle_diff = max(max_angle_diff, diff)
            
            # Wide angle spread = flank risk
            flank_risk = min(100, max_angle_diff * 0.5)
            self.positioning.flank_risk = flank_risk
            
            # Pinch risk (enemies on opposite sides)
            if max_angle_diff > 150:
                self.positioning.pinch_risk = min(100, max_angle_diff * 0.8)
            else:
                self.positioning.pinch_risk = max(0, max_angle_diff * 0.3)
        else:
            self.positioning.flank_risk = 10
            self.positioning.pinch_risk = 0
    
    def _determine_engagement_type(self, nearest_enemy_distance: float) -> None:
        """Determine engagement type based on enemy distance"""
        if nearest_enemy_distance < 25:
            self.positioning.engagement_type = EngagementType.CLOSE_QUARTERS
        elif nearest_enemy_distance < 100:
            self.positioning.engagement_type = EngagementType.MEDIUM_RANGE
        elif nearest_enemy_distance < 300:
            self.positioning.engagement_type = EngagementType.LONG_RANGE
        elif nearest_enemy_distance < 99999:
            self.positioning.engagement_type = EngagementType.EXTREME_RANGE
        else:
            self.positioning.engagement_type = EngagementType.UNKNOWN
    
    def _analyze_zone_positioning(self) -> None:
        """Analyze positioning relative to zone"""
        if not self.positioning.player_positions:
            return
        
        cx = self.positioning.center_x
        cy = self.positioning.center_y
        
        # Check distance to zone center (good for center control strategy)
        # Check distance to zone edge (good for edge play)
        
        if self.positioning.next_zone_prediction:
            nzx, nzy = self.positioning.next_zone_prediction
            dist_to_next = math.sqrt((cx - nzx) ** 2 + (cy - nzy) ** 2)
            
            # Being close to next zone is good
            if dist_to_next < 200:
                self.positioning.zone_position_quality = 80
            elif dist_to_next < 500:
                self.positioning.zone_position_quality = 50
            else:
                self.positioning.zone_position_quality = 20
        else:
            self.positioning.zone_position_quality = 50
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RECOMMENDATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_position_recommendations(self) -> List[str]:
        """Get tactical recommendations based on positioning"""
        recommendations = []
        
        if self.positioning.overall_quality in [PositionQuality.POOR, PositionQuality.DANGEROUS]:
            recommendations.append("⚠️ Current position is dangerous - consider rotating")
        
        if self.positioning.players_exposed >= 3:
            recommendations.append("🛡️ Multiple players exposed - seek cover")
        
        if self.positioning.flank_risk > 60:
            recommendations.append("⚠️ High flank risk - watch your sides")
        
        if self.positioning.pinch_risk > 50:
            recommendations.append("🔴 Risk of being pinched - reposition immediately")
        
        if self.positioning.spread_radius > 100:
            recommendations.append("↔️ Team too spread out - tighten formation")
        
        if self.positioning.formation == FormationType.STACKED:
            recommendations.append("📦 Team too close together - spread out")
        
        if not self.positioning.in_zone and self.positioning.distance_to_zone_edge > 100:
            recommendations.append("🏃 Need to move to zone soon")
        
        if any(p.is_isolated for p in self.positioning.player_positions.values()):
            recommendations.append("⚠️ Isolated player(s) - regroup with team")
        
        if self.positioning.average_cover_quality < 30:
            recommendations.append("🧱 Poor cover - find better position")
        
        if self.positioning.nearest_enemy_distance < 15 and self.positioning.formation == FormationType.SCATTERED:
            recommendations.append("⚔️ Close enemy, scattered formation - regroup for fight")
        
        return recommendations
    
    def get_ideal_rotation_path(
        self,
        target_x: float,
        target_y: float,
    ) -> List[Tuple[float, float]]:
        """Calculate ideal rotation path considering cover"""
        if not self.positioning.player_positions:
            return [(target_x, target_y)]
        
        cx = self.positioning.center_x
        cy = self.positioning.center_y
        
        path = [(cx, cy)]
        
        # Add waypoints at compound locations for cover
        for comp_x, comp_y, radius, name, quality in self.compound_locations:
            # Check if compound is between current position and target
            if quality > 60:  # Only good compounds
                # Simple interpolation - in production would use pathfinding
                mid_x = (cx + target_x) / 2
                mid_y = (cy + target_y) / 2
                
                dist_to_compound = math.sqrt(
                    (mid_x - comp_x) ** 2 + (mid_y - comp_y) ** 2
                )
                
                if dist_to_compound < 500:  # Compound is near path
                    path.append((comp_x, comp_y))
        
        path.append((target_x, target_y))
        return path
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_positioning_summary(self) -> Dict[str, Any]:
        """Get positioning analysis summary"""
        return {
            "quality": {
                "overall": self.positioning.overall_quality.value,
                "score": round(self.positioning.quality_score, 1),
                "defensive": round(self.positioning.defensive_score, 1),
                "offensive": round(self.positioning.offensive_score, 1),
            },
            "formation": {
                "type": self.positioning.formation.value,
                "spread_radius": round(self.positioning.spread_radius, 1),
                "average_spacing": round(self.positioning.average_spacing, 1),
            },
            "cover": {
                "players_in_cover": self.positioning.players_in_cover,
                "players_exposed": self.positioning.players_exposed,
                "average_quality": round(self.positioning.average_cover_quality, 1),
            },
            "zone": {
                "in_zone": self.positioning.in_zone,
                "distance_to_edge": round(self.positioning.distance_to_zone_edge, 1),
                "position_quality": round(self.positioning.zone_position_quality, 1),
            },
            "threats": {
                "nearest_enemy_distance": round(self.positioning.nearest_enemy_distance, 1),
                "engagement_type": self.positioning.engagement_type.value,
                "flank_risk": round(self.positioning.flank_risk, 1),
                "pinch_risk": round(self.positioning.pinch_risk, 1),
            },
            "compound": {
                "in_compound": self.positioning.in_compound,
                "name": self.positioning.compound_name,
                "quality": round(self.positioning.compound_quality, 1),
            } if self.positioning.in_compound else None,
            "recommendations": self.get_position_recommendations(),
            "players": {
                pid: {
                    "name": p.player_name,
                    "cover": p.cover_type.value,
                    "cover_quality": round(p.cover_quality, 1),
                    "exposed": p.is_exposed,
                    "exposure": round(p.exposure_percentage, 1),
                    "high_ground": p.has_high_ground,
                    "isolated": p.is_isolated,
                    "distance_to_teammate": round(p.distance_to_nearest_teammate, 1),
                }
                for pid, p in self.positioning.player_positions.items()
            },
        }
    
    def get_quality_trend(self) -> List[float]:
        """Get positioning quality trend"""
        return list(self.history.quality_history)
    
    def reset_for_new_match(self) -> None:
        """Reset for new match"""
        self.positioning = TeamPositioning(team_id=self.team_id)
        self.history = PositionHistory()
        self._log.info("Positioning analyzer reset for new match")