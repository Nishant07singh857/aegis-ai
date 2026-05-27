# backend/app/agents/team_agents/team_tracker.py
"""
AEGIS AI - Team Tracker
Core team state tracking with real-time player monitoring,
HP tracking, knock/death detection, and team status management.
"""

import time
import math
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.team_tracker")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PlayerState(str, Enum):
    """Player alive state"""
    ALIVE = "alive"
    KNOCKED = "knocked"
    DEAD = "dead"
    REVIVING = "reviving"
    DISCONNECTED = "disconnected"

class TeamActivity(str, Enum):
    """Current team activity"""
    LOOTING = "looting"
    ROTATING = "rotating"
    FIGHTING = "fighting"
    HEALING = "healing"
    CAMPING = "camping"
    REVIVING = "reviving"
    ELIMINATED = "eliminated"

class ThreatLevel(str, Enum):
    """Threat assessment"""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

@dataclass
class PlayerStateTracker:
    """Detailed player state tracking"""
    player_id: str
    player_name: str
    team_id: str
    
    # Core state
    state: PlayerState = PlayerState.ALIVE
    hp: int = 100
    max_hp: int = 100
    armor: int = 100
    max_armor: int = 100
    boost_level: int = 0  # 0-3
    
    # Position
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 0.0
    rotation_yaw: float = 0.0  # Where player is looking
    last_position_update: float = 0.0
    movement_speed: float = 0.0  # meters per second
    
    # Combat stats
    kills: int = 0
    knocks: int = 0
    assists: int = 0
    damage_dealt: int = 0
    damage_taken: int = 0
    headshots: int = 0
    longest_kill: float = 0.0  # meters
    
    # Survival stats
    revives_performed: int = 0
    times_knocked: int = 0
    times_revived: int = 0
    healing_done: int = 0
    healing_received: int = 0
    
    # Equipment
    current_weapon: str = "unarmed"
    secondary_weapon: str = "unarmed"
    has_helmet: bool = False
    has_vest: bool = False
    helmet_level: int = 0
    vest_level: int = 0
    
    # Utility inventory
    smokes: int = 0
    grenades: int = 0
    molotovs: int = 0
    flashes: int = 0
    first_aids: int = 0
    bandages: int = 0
    boosts: int = 0
    
    # Combat state
    is_aiming: bool = False
    is_firing: bool = False
    is_reloading: bool = False
    is_healing: bool = False
    is_boosting: bool = False
    is_sprinting: bool = False
    is_crouching: bool = False
    is_proning: bool = False
    is_in_vehicle: bool = False
    is_swimming: bool = False
    is_falling: bool = False
    
    # Threat tracking
    under_attack: bool = False
    attacker_id: Optional[str] = None
    attacker_team_id: Optional[str] = None
    last_damage_time: float = 0.0
    recent_damage_taken: int = 0  # Last 2 seconds
    threat_level: ThreatLevel = ThreatLevel.NONE
    
    # Position history for movement analysis
    position_history: deque = field(default_factory=lambda: deque(maxlen=50))
    
    def is_alive(self) -> bool:
        """Check if player can act"""
        return self.state in [PlayerState.ALIVE, PlayerState.REVIVING]
    
    def is_combat_capable(self) -> bool:
        """Check if player can fight"""
        return self.state == PlayerState.ALIVE and self.hp > 0
    
    def get_position(self) -> Tuple[float, float, float]:
        """Get current position"""
        return (self.position_x, self.position_y, self.position_z)

@dataclass
class TeamStateTracker:
    """Complete team state tracking"""
    team_id: str
    team_name: str
    short_name: str
    
    # Players
    players: List[PlayerStateTracker] = field(default_factory=list)
    
    # Team status
    activity: TeamActivity = TeamActivity.LOOTING
    previous_activity: TeamActivity = TeamActivity.LOOTING
    activity_changed_at: float = field(default_factory=time.time)
    
    # Position
    center_x: float = 0.0
    center_y: float = 0.0
    center_z: float = 0.0
    formation_spread: float = 0.0  # Max distance between players
    formation_type: str = "unknown"  # line, circle, spread, stacked
    
    # Movement
    is_moving: bool = False
    movement_direction: float = 0.0  # Degrees
    movement_speed: float = 0.0  # Team average speed
    distance_traveled: float = 0.0
    
    # Survival
    alive_count: int = 4
    knocked_count: int = 0
    dead_count: int = 0
    full_dead_count: int = 0  # Cannot be revived
    
    # Combat
    total_kills: int = 0
    total_knocks: int = 0
    total_assists: int = 0
    total_damage_dealt: int = 0
    total_damage_taken: int = 0
    total_headshots: int = 0
    
    # Utility usage
    total_smokes_used: int = 0
    total_grenades_used: int = 0
    total_molotovs_used: int = 0
    total_flashes_used: int = 0
    total_healing: int = 0
    total_boosts: int = 0
    
    # Zone status
    zone_phase: int = 0
    in_zone: bool = True
    distance_to_zone: float = 0.0
    zone_damage_taken: int = 0
    
    # Vehicle
    has_vehicle: bool = False
    vehicle_type: Optional[str] = None
    vehicle_hp: int = 0
    
    # Threat assessment
    threat_level: ThreatLevel = ThreatLevel.NONE
    nearby_enemies: int = 0
    nearest_enemy_distance: float = 99999.0
    
    # Match tracking
    match_start_time: float = field(default_factory=time.time)
    last_update_time: float = field(default_factory=time.time)
    
    # HP history for analysis
    team_hp_history: deque = field(default_factory=lambda: deque(maxlen=100))
    
    def get_alive_players(self) -> List[PlayerStateTracker]:
        """Get players who can still act"""
        return [p for p in self.players if p.is_alive()]
    
    def get_combat_ready(self) -> List[PlayerStateTracker]:
        """Get players ready to fight"""
        return [p for p in self.players if p.is_combat_capable()]
    
    def get_knocked_players(self) -> List[PlayerStateTracker]:
        """Get knocked players needing revive"""
        return [p for p in self.players if p.state == PlayerState.KNOCKED]
    
    def get_total_hp(self) -> int:
        """Get total team HP"""
        return sum(p.hp for p in self.players if p.is_alive())
    
    def get_total_hp_percent(self) -> float:
        """Get team HP as percentage"""
        alive = self.get_alive_players()
        if not alive:
            return 0.0
        total_max = sum(p.max_hp for p in alive)
        total_current = sum(p.hp for p in alive)
        return (total_current / total_max * 100) if total_max > 0 else 0.0
    
    def is_eliminated(self) -> bool:
        """Check if team is completely eliminated"""
        return (
            self.alive_count == 0 
            and self.knocked_count == 0 
            and self.full_dead_count == 4
        )
    
    def is_wipable(self) -> bool:
        """Check if team can be wiped (all alive are knocked)"""
        return self.alive_count == 0 and self.knocked_count > 0


class TeamTracker:
    """
    Core team state tracker.
    
    Tracks:
    - All 4 players with detailed state
    - Team position and formation
    - HP/armor/equipment for each player
    - Combat stats (kills, damage, headshots)
    - Survival stats (revives, healing)
    - Zone status
    - Threat assessment
    - Activity detection
    """
    
    def __init__(self, team_id: str, team_name: str, short_name: str):
        self.team = TeamStateTracker(
            team_id=team_id,
            team_name=team_name,
            short_name=short_name,
        )
        
        # Configuration
        self.position_history_size = 50
        self.hp_history_size = 100
        
        # Tracking state
        self._last_position_broadcast = 0.0
        self._position_broadcast_interval = 0.5  # seconds
        
        self._log = logger.bind(
            team_name=team_name,
            team_id=team_id,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYER MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_player(self, player_id: str, player_name: str) -> PlayerStateTracker:
        """Add a player to the team"""
        player = PlayerStateTracker(
            player_id=player_id,
            player_name=player_name,
            team_id=self.team.team_id,
        )
        self.team.players.append(player)
        self._log.debug(f"Player added: {player_name} ({player_id})")
        return player
    
    def get_player(self, player_id: str) -> Optional[PlayerStateTracker]:
        """Get player by ID"""
        for player in self.team.players:
            if player.player_id == player_id:
                return player
        return None
    
    def get_player_by_name(self, name: str) -> Optional[PlayerStateTracker]:
        """Get player by name"""
        for player in self.team.players:
            if player.player_name.lower() == name.lower():
                return player
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATE UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_player_hp(self, player_id: str, hp: int, armor: int = -1) -> bool:
        """Update player HP and armor"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        old_hp = player.hp
        player.hp = max(0, min(hp, player.max_hp))
        
        if armor >= 0:
            player.armor = max(0, min(armor, player.max_armor))
        
        # Track damage taken
        if player.hp < old_hp:
            damage = old_hp - player.hp
            player.damage_taken += damage
            player.recent_damage_taken += damage
            player.last_damage_time = time.time()
            
            if damage > 20:
                player.under_attack = True
        
        # Update team totals
        self._update_team_survival_counts()
        self._record_team_hp()
        
        return True
    
    def update_player_position(
        self,
        player_id: str,
        x: float,
        y: float,
        z: float = 0.0,
        yaw: float = 0.0,
    ) -> bool:
        """Update player position"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        # Calculate movement speed
        if player.last_position_update > 0:
            dt = time.time() - player.last_position_update
            if dt > 0:
                distance = math.sqrt(
                    (x - player.position_x) ** 2 + 
                    (y - player.position_y) ** 2
                )
                player.movement_speed = distance / dt
                self.team.distance_traveled += distance
        
        # Update position
        player.position_x = x
        player.position_y = y
        player.position_z = z
        player.rotation_yaw = yaw
        player.last_position_update = time.time()
        
        # Store in history
        player.position_history.append((x, y, z, time.time()))
        
        # Update team center
        self._update_team_center()
        
        return True
    
    def update_player_state(self, player_id: str, new_state: PlayerState) -> bool:
        """Update player alive state"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        old_state = player.state
        player.state = new_state
        
        # Update counters
        if new_state == PlayerState.KNOCKED and old_state == PlayerState.ALIVE:
            player.times_knocked += 1
        elif new_state == PlayerState.DEAD:
            if old_state == PlayerState.KNOCKED:
                pass  # Was already counted as knocked
            else:
                player.times_knocked += 1
        
        self._update_team_survival_counts()
        self._detect_team_activity()
        
        self._log.debug(
            f"Player state changed: {player.player_name} "
            f"{old_state.value} -> {new_state.value}"
        )
        
        return True
    
    def update_player_weapon(self, player_id: str, weapon_name: str, slot: int = 0) -> bool:
        """Update player's current weapon"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        if slot == 0:
            player.current_weapon = weapon_name
        else:
            player.secondary_weapon = weapon_name
        
        return True
    
    def update_player_equipment(
        self,
        player_id: str,
        helmet_level: int = -1,
        vest_level: int = -1,
    ) -> bool:
        """Update player equipment"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        if helmet_level >= 0:
            player.helmet_level = helmet_level
            player.has_helmet = helmet_level > 0
        
        if vest_level >= 0:
            player.vest_level = vest_level
            player.has_vest = vest_level > 0
        
        return True
    
    def update_player_utility(
        self,
        player_id: str,
        item_type: str,
        count: int,
    ) -> bool:
        """Update player utility inventory"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        utility_map = {
            "smoke": "smokes",
            "grenade": "grenades",
            "molotov": "molotovs",
            "flash": "flashes",
            "first_aid": "first_aids",
            "bandage": "bandages",
            "boost": "boosts",
        }
        
        attr_name = utility_map.get(item_type)
        if attr_name and hasattr(player, attr_name):
            setattr(player, attr_name, max(0, count))
            return True
        
        return False
    
    def record_kill(
        self,
        killer_id: str,
        victim_id: str,
        weapon: str,
        damage: int,
        distance: float,
        headshot: bool = False,
    ) -> bool:
        """Record a kill by a team player"""
        player = self.get_player(killer_id)
        if not player:
            return False
        
        player.kills += 1
        player.damage_dealt += damage
        self.team.total_kills += 1
        self.team.total_damage_dealt += damage
        
        if headshot:
            player.headshots += 1
            self.team.total_headshots += 1
        
        if distance > player.longest_kill:
            player.longest_kill = distance
        
        return True
    
    def record_knock(self, knocker_id: str) -> bool:
        """Record a knock by a team player"""
        player = self.get_player(knocker_id)
        if not player:
            return False
        
        player.knocks += 1
        self.team.total_knocks += 1
        return True
    
    def record_revive(self, reviver_id: str, revived_id: str) -> bool:
        """Record a revive"""
        reviver = self.get_player(reviver_id)
        revived = self.get_player(revived_id)
        
        if reviver:
            reviver.revives_performed += 1
        
        if revived:
            revived.times_revived += 1
            revived.state = PlayerState.ALIVE
        
        self._update_team_survival_counts()
        return True
    
    def record_healing(self, player_id: str, amount: int, heal_type: str = "first_aid") -> bool:
        """Record healing"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        player.healing_done += amount
        self.team.total_healing += amount
        
        # Update HP
        player.hp = min(player.max_hp, player.hp + amount)
        
        return True
    
    def record_utility_used(self, player_id: str, utility_type: str) -> bool:
        """Record utility usage"""
        player = self.get_player(player_id)
        if not player:
            return False
        
        utility_counters = {
            "smoke": "smokes",
            "grenade": "grenades",
            "molotov": "molotovs",
            "flash": "flashes",
            "heal": "first_aids",
            "boost": "boosts",
        }
        
        # Decrement inventory
        attr = utility_counters.get(utility_type)
        if attr and hasattr(player, attr):
            current = getattr(player, attr)
            setattr(player, attr, max(0, current - 1))
        
        # Increment team totals
        team_counters = {
            "smoke": "total_smokes_used",
            "grenade": "total_grenades_used",
            "molotov": "total_molotovs_used",
            "flash": "total_flashes_used",
        }
        
        team_attr = team_counters.get(utility_type)
        if team_attr and hasattr(self.team, team_attr):
            setattr(self.team, team_attr, getattr(self.team, team_attr) + 1)
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TEAM CALCULATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_team_center(self) -> None:
        """Calculate team center position"""
        alive = self.team.get_alive_players()
        if not alive:
            return
        
        avg_x = sum(p.position_x for p in alive) / len(alive)
        avg_y = sum(p.position_y for p in alive) / len(alive)
        avg_z = sum(p.position_z for p in alive) / len(alive)
        
        self.team.center_x = avg_x
        self.team.center_y = avg_y
        self.team.center_z = avg_z
        
        # Calculate formation spread
        if len(alive) > 1:
            spreads = []
            for p in alive:
                dist = math.sqrt(
                    (p.position_x - avg_x) ** 2 + 
                    (p.position_y - avg_y) ** 2
                )
                spreads.append(dist)
            self.team.formation_spread = max(spreads)
            self._analyze_formation(alive, avg_x, avg_y)
        else:
            self.team.formation_spread = 0.0
            self.team.formation_type = "solo"
    
    def _analyze_formation(
        self,
        players: List[PlayerStateTracker],
        center_x: float,
        center_y: float,
    ) -> None:
        """Analyze team formation type"""
        if len(players) < 2:
            self.team.formation_type = "solo"
            return
        
        # Calculate relative positions
        positions = []
        for p in players:
            rel_x = p.position_x - center_x
            rel_y = p.position_y - center_y
            angle = math.degrees(math.atan2(rel_y, rel_x))
            distance = math.sqrt(rel_x ** 2 + rel_y ** 2)
            positions.append((angle, distance))
        
        max_distance = max(d for _, d in positions)
        min_distance = min(d for _, d in positions)
        
        # Determine formation type
        if max_distance < 10:
            self.team.formation_type = "stacked"
        elif max_distance > 100:
            self.team.formation_type = "spread"
        else:
            # Check if in line formation
            angles = sorted([a for a, _ in positions])
            angle_spread = max(angles) - min(angles)
            if angle_spread > 270:
                self.team.formation_type = "circle"
            elif angle_spread < 90:
                self.team.formation_type = "line"
            else:
                self.team.formation_type = "scattered"
    
    def _update_team_survival_counts(self) -> None:
        """Update team survival counts"""
        alive = 0
        knocked = 0
        dead = 0
        
        for player in self.team.players:
            if player.state == PlayerState.ALIVE:
                alive += 1
            elif player.state == PlayerState.KNOCKED:
                knocked += 1
            elif player.state == PlayerState.DEAD:
                dead += 1
        
        self.team.alive_count = alive
        self.team.knocked_count = knocked
        self.team.dead_count = dead
    
    def _record_team_hp(self) -> None:
        """Record team HP history"""
        total_hp = sum(
            p.hp for p in self.team.players 
            if p.state in [PlayerState.ALIVE, PlayerState.REVIVING]
        )
        total_max = sum(
            p.max_hp for p in self.team.players 
            if p.state in [PlayerState.ALIVE, PlayerState.REVIVING]
        )
        
        hp_percent = (total_hp / total_max * 100) if total_max > 0 else 0
        
        self.team.team_hp_history.append({
            "timestamp": time.time(),
            "total_hp": total_hp,
            "hp_percent": hp_percent,
            "alive_count": self.team.alive_count,
        })
    
    def _detect_team_activity(self) -> None:
        """Detect current team activity based on state"""
        previous = self.team.activity
        
        if self.team.is_eliminated():
            new_activity = TeamActivity.ELIMINATED
        elif self.team.knocked_count > 0:
            if self.team.alive_count > 0:
                new_activity = TeamActivity.REVIVING
            else:
                new_activity = TeamActivity.FIGHTING
        elif any(p.under_attack for p in self.team.players):
            new_activity = TeamActivity.FIGHTING
        elif self.team.is_moving and self.team.movement_speed > 5:
            new_activity = TeamActivity.ROTATING
        elif any(p.is_healing for p in self.team.players):
            new_activity = TeamActivity.HEALING
        else:
            new_activity = TeamActivity.CAMPING
        
        if new_activity != previous:
            self.team.previous_activity = previous
            self.team.activity = new_activity
            self.team.activity_changed_at = time.time()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # THREAT ASSESSMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def assess_threat(
        self,
        nearby_enemy_count: int = 0,
        nearest_enemy_distance: float = 99999.0,
        under_attack: bool = False,
        zone_damage: bool = False,
    ) -> ThreatLevel:
        """Assess current threat level"""
        threat_score = 0
        
        # Enemy proximity
        if nearest_enemy_distance < 10:
            threat_score += 40
        elif nearest_enemy_distance < 50:
            threat_score += 25
        elif nearest_enemy_distance < 100:
            threat_score += 10
        
        # Number of enemies
        threat_score += nearby_enemy_count * 5
        
        # Under direct attack
        if under_attack:
            threat_score += 30
        
        # Team HP status
        hp_percent = self.team.get_total_hp_percent()
        if hp_percent < 25:
            threat_score += 25
        elif hp_percent < 50:
            threat_score += 15
        elif hp_percent < 75:
            threat_score += 5
        
        # Manpower disadvantage
        if self.team.alive_count < 2:
            threat_score += 20
        elif self.team.alive_count < 3:
            threat_score += 10
        
        # Zone damage
        if zone_damage:
            threat_score += 15
        
        # Outside zone
        if not self.team.in_zone and self.team.zone_phase > 2:
            threat_score += 10
        
        # Determine threat level
        if threat_score >= 80:
            level = ThreatLevel.CRITICAL
        elif threat_score >= 50:
            level = ThreatLevel.HIGH
        elif threat_score >= 25:
            level = ThreatLevel.MEDIUM
        elif threat_score >= 10:
            level = ThreatLevel.LOW
        else:
            level = ThreatLevel.NONE
        
        self.team.threat_level = level
        self.team.nearby_enemies = nearby_enemy_count
        self.team.nearest_enemy_distance = nearest_enemy_distance
        
        return level
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DATA EXPORT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_team_summary(self) -> Dict[str, Any]:
        """Get comprehensive team summary"""
        return {
            "team_id": self.team.team_id,
            "team_name": self.team.team_name,
            "short_name": self.team.short_name,
            "activity": self.team.activity.value,
            "alive_count": self.team.alive_count,
            "knocked_count": self.team.knocked_count,
            "dead_count": self.team.dead_count,
            "total_hp_percent": round(self.team.get_total_hp_percent(), 1),
            "position": {
                "x": round(self.team.center_x, 1),
                "y": round(self.team.center_y, 1),
            },
            "formation": {
                "type": self.team.formation_type,
                "spread": round(self.team.formation_spread, 1),
            },
            "combat": {
                "total_kills": self.team.total_kills,
                "total_knocks": self.team.total_knocks,
                "total_damage": self.team.total_damage_dealt,
                "damage_taken": self.team.total_damage_taken,
                "headshots": self.team.total_headshots,
            },
            "utility": {
                "smokes": self.team.total_smokes_used,
                "grenades": self.team.total_grenades_used,
                "molotovs": self.team.total_molotovs_used,
                "healing": self.team.total_healing,
            },
            "zone": {
                "phase": self.team.zone_phase,
                "in_zone": self.team.in_zone,
                "distance_to_zone": round(self.team.distance_to_zone, 1),
            },
            "threat": {
                "level": self.team.threat_level.value,
                "nearby_enemies": self.team.nearby_enemies,
                "nearest_enemy_distance": round(self.team.nearest_enemy_distance, 1),
            },
            "players": [
                {
                    "player_id": p.player_id,
                    "name": p.player_name,
                    "state": p.state.value,
                    "hp": p.hp,
                    "armor": p.armor,
                    "position": {
                        "x": round(p.position_x, 1),
                        "y": round(p.position_y, 1),
                    },
                    "weapon": p.current_weapon,
                    "kills": p.kills,
                    "damage": p.damage_dealt,
                    "under_attack": p.under_attack,
                }
                for p in self.team.players
            ],
        }
    
    def get_player_detail(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed player information"""
        player = self.get_player(player_id)
        if not player:
            return None
        
        return {
            "player_id": player.player_id,
            "name": player.player_name,
            "team_id": player.team_id,
            "state": player.state.value,
            "hp": player.hp,
            "max_hp": player.max_hp,
            "armor": player.armor,
            "boost_level": player.boost_level,
            "position": {
                "x": round(player.position_x, 1),
                "y": round(player.position_y, 1),
                "z": round(player.position_z, 1),
            },
            "movement_speed": round(player.movement_speed, 1),
            "weapons": {
                "primary": player.current_weapon,
                "secondary": player.secondary_weapon,
            },
            "equipment": {
                "helmet": player.helmet_level,
                "vest": player.vest_level,
            },
            "inventory": {
                "smokes": player.smokes,
                "grenades": player.grenades,
                "molotovs": player.molotovs,
                "heals": player.first_aids,
                "boosts": player.boosts,
            },
            "combat": {
                "kills": player.kills,
                "knocks": player.knocks,
                "assists": player.assists,
                "damage_dealt": player.damage_dealt,
                "damage_taken": player.damage_taken,
                "headshots": player.headshots,
                "longest_kill": round(player.longest_kill, 1),
            },
            "survival": {
                "revives_performed": player.revives_performed,
                "times_knocked": player.times_knocked,
                "times_revived": player.times_revived,
                "healing_done": player.healing_done,
            },
            "status": {
                "under_attack": player.under_attack,
                "threat_level": player.threat_level.value,
                "is_healing": player.is_healing,
                "is_sprinting": player.is_sprinting,
                "is_in_vehicle": player.is_in_vehicle,
            },
        }
    
    def reset_for_new_match(self) -> None:
        """Reset tracking for a new match"""
        self.team = TeamStateTracker(
            team_id=self.team.team_id,
            team_name=self.team.team_name,
            short_name=self.team.short_name,
        )
        
        # Re-create players
        old_players = self.team.players
        self.team.players = []
        for old in old_players:
            self.add_player(old.player_id, old.player_name)
        
        self._log.info("Team tracker reset for new match")