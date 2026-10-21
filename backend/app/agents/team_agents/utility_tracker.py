# backend/app/agents/team_agents/utility_tracker.py
"""
AEGIS AI - Utility Tracker
Tracks all utility usage: grenades, smokes, molotovs, flashes,
healing items, boosts, and analyzes usage patterns.
"""

import time
from typing import Dict, List, Optional, Any, Deque
from dataclasses import dataclass
from collections import deque, defaultdict
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.utility_tracker")

class UtilityType(str, Enum):
    """Types of utility items"""
    SMOKE = "smoke"
    GRENADE = "grenade"
    MOLOTOV = "molotov"
    FLASH = "flash"
    # Phase 23 (W3): world-side stun/flashbang observations arrive from
    # the vision pipeline as utility_type="stun" (see vision runtime).
    STUN = "stun"
    FIRST_AID = "first_aid"
    BANDAGE = "bandage"
    BOOST = "boost"
    MEDKIT = "medkit"

class UtilityResult(str, Enum):
    """Result of utility usage"""
    HIT = "hit"           # Hit enemy
    MISS = "miss"         # Missed
    ZONE = "zone"         # Used for zone denial
    COVER = "cover"       # Used for cover
    HEAL = "heal"         # Healing completed
    UNKNOWN = "unknown"

@dataclass
class UtilityEvent:
    """Single utility usage event"""
    event_id: str
    player_id: str
    player_name: str
    utility_type: UtilityType
    timestamp: float
    location_x: float
    location_y: float
    result: UtilityResult = UtilityResult.UNKNOWN
    target_x: Optional[float] = None
    target_y: Optional[float] = None
    damage_dealt: int = 0
    enemies_hit: int = 0
    healing_amount: int = 0
    fight_id: Optional[str] = None
    zone_phase: int = 0

@dataclass
class PlayerUtilityStats:
    """Per-player utility statistics"""
    player_id: str
    player_name: str
    
    # Usage counts
    smokes_used: int = 0
    grenades_used: int = 0
    molotovs_used: int = 0
    flashes_used: int = 0
    stuns_used: int = 0
    healing_done: int = 0
    boosts_used: int = 0
    
    # Effectiveness
    grenade_hits: int = 0
    grenade_kills: int = 0
    molotov_hits: int = 0
    molotov_kills: int = 0
    flash_hits: int = 0
    smoke_effectiveness: float = 0.0
    
    # Healing
    healing_received: int = 0
    
    # Timing
    avg_utility_use_per_minute: float = 0.0

@dataclass
class TeamUtilityStats:
    """Team-wide utility statistics"""
    team_id: str
    
    # Totals
    total_smokes: int = 0
    total_grenades: int = 0
    total_molotovs: int = 0
    total_flashes: int = 0
    total_stuns: int = 0
    total_healing: int = 0
    total_boosts: int = 0
    
    # Effectiveness
    grenade_accuracy: float = 0.0  # Hits / Thrown
    molotov_accuracy: float = 0.0
    flash_accuracy: float = 0.0
    smoke_success_rate: float = 0.0
    
    # Patterns
    prefers_smoke_pushes: bool = False
    grenade_heavy: bool = False
    healing_frequency: str = "normal"  # low, normal, high
    
    # Timing
    utility_per_fight: float = 0.0
    healing_per_minute: float = 0.0


class UtilityTracker(BaseAgent):
    """
    Tracks all utility usage across the team.
    
    Tracks:
    - Every utility usage event
    - Per-player and per-team statistics
    - Utility effectiveness (hits, kills, accuracy)
    - Healing patterns
    - Utility inventory management
    - Smoke coverage analysis
    - Grenade trajectory tracking
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus, team_id: str):
        super().__init__(config, event_bus)
        self.team_id = team_id
        
        # Events
        self.utility_events: Deque[UtilityEvent] = deque(maxlen=200)
        
        # Player stats
        self.player_stats: Dict[str, PlayerUtilityStats] = {}
        
        # Team stats
        self.team_stats = TeamUtilityStats(team_id=team_id)
        
        # Active smokes (for coverage analysis)
        self.active_smokes: List[Dict[str, Any]] = []
        
        # Healing tracking
        self.active_heals: Dict[str, Dict[str, Any]] = {}
        
        # Match timing
        self.match_start_time: float = time.time()
        
        # Inventory tracking
        self.player_inventories: Dict[str, Dict[str, int]] = {}
        
        self._log = logger.bind(team_id=team_id)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITY USAGE RECORDING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_utility_use(
        self,
        event_id: str,
        player_id: str,
        player_name: str,
        utility_type: UtilityType,
        location_x: float,
        location_y: float,
        target_x: Optional[float] = None,
        target_y: Optional[float] = None,
        result: UtilityResult = UtilityResult.UNKNOWN,
        damage_dealt: int = 0,
        enemies_hit: int = 0,
        fight_id: Optional[str] = None,
        zone_phase: int = 0,
    ) -> UtilityEvent:
        """Record a utility usage event"""
        event = UtilityEvent(
            event_id=event_id,
            player_id=player_id,
            player_name=player_name,
            utility_type=utility_type,
            timestamp=time.time(),
            location_x=location_x,
            location_y=location_y,
            target_x=target_x,
            target_y=target_y,
            result=result,
            damage_dealt=damage_dealt,
            enemies_hit=enemies_hit,
            fight_id=fight_id,
            zone_phase=zone_phase,
        )
        
        self.utility_events.append(event)
        
        # Update player stats
        self._update_player_stats(player_id, player_name, event)
        
        # Update team stats
        self._update_team_stats(event)
        
        # Track active smokes
        if utility_type == UtilityType.SMOKE:
            self._track_smoke(event)
        
        self._log.debug(
            f"Utility used: {player_name} - {utility_type.value}",
            result=result.value,
            damage=damage_dealt,
        )
        
        return event
    
    def record_healing(
        self,
        player_id: str,
        player_name: str,
        heal_type: UtilityType,
        amount: int,
        location_x: float,
        location_y: float,
        interrupted: bool = False,
    ) -> None:
        """Record healing usage"""
        event = UtilityEvent(
            event_id=f"heal_{int(time.time() * 1000)}",
            player_id=player_id,
            player_name=player_name,
            utility_type=heal_type,
            timestamp=time.time(),
            location_x=location_x,
            location_y=location_y,
            healing_amount=amount if not interrupted else 0,
            result=UtilityResult.HEAL if not interrupted else UtilityResult.MISS,
        )
        
        self.utility_events.append(event)
        self._update_player_stats(player_id, player_name, event)
        self._update_team_stats(event)
        
        if not interrupted:
            self.team_stats.total_healing += amount
        
        self._log.debug(
            f"Healing: {player_name} - {heal_type.value} ({amount}HP)"
            f"{' [INTERRUPTED]' if interrupted else ''}"
        )
    
    def record_grenade_kill(
        self,
        player_id: str,
        event_id: str,
    ) -> None:
        """Record a grenade kill"""
        self._log.info(f"Grenade kill by {player_id}")
        
        # Update the original event
        for event in self.utility_events:
            if event.event_id == event_id:
                event.result = UtilityResult.HIT
                event.enemies_hit += 1
                break
        
        # Update player stats
        if player_id in self.player_stats:
            self.player_stats[player_id].grenade_kills += 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INVENTORY MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update_inventory(
        self,
        player_id: str,
        items: Dict[str, int],
    ) -> None:
        """Update player's utility inventory"""
        self.player_inventories[player_id] = items
    
    def get_inventory(self, player_id: str) -> Dict[str, int]:
        """Get player's current inventory"""
        return self.player_inventories.get(player_id, {})
    
    def get_team_inventory_total(self) -> Dict[str, int]:
        """Get total team utility inventory"""
        totals = defaultdict(int)
        for inventory in self.player_inventories.values():
            for item, count in inventory.items():
                totals[item] += count
        return dict(totals)
    
    def can_smoke_push(self) -> bool:
        """Check if team has enough smokes for a smoke push"""
        totals = self.get_team_inventory_total()
        return totals.get("smokes", 0) >= 6  # At least 6 smokes for push
    
    def can_spam_grenades(self) -> bool:
        """Check if team has enough grenades to spam"""
        totals = self.get_team_inventory_total()
        return totals.get("grenades", 0) >= 4
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SMOKE TRACKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _track_smoke(self, event: UtilityEvent) -> None:
        """Track active smoke grenades"""
        self.active_smokes.append({
            "event_id": event.event_id,
            "player_id": event.player_id,
            "location_x": event.location_x,
            "location_y": event.location_y,
            "deployed_at": time.time(),
            "duration": 25.0,  # Smoke lasts ~25 seconds
        })
        
        # Clean up expired smokes
        self._cleanup_smokes()
    
    def _cleanup_smokes(self) -> None:
        """Remove expired smokes"""
        current_time = time.time()
        self.active_smokes = [
            s for s in self.active_smokes
            if current_time - s["deployed_at"] < s["duration"]
        ]
    
    def get_active_smokes(self) -> List[Dict[str, Any]]:
        """Get currently active smokes"""
        self._cleanup_smokes()
        return self.active_smokes
    
    def get_smoke_coverage(self, x: float, y: float, radius: float = 10.0) -> float:
        """Check if a position is covered by smoke (0-100%)"""
        active = self.get_active_smokes()
        
        for smoke in active:
            distance = (
                (x - smoke["location_x"]) ** 2 + 
                (y - smoke["location_y"]) ** 2
            ) ** 0.5
            
            if distance < radius:
                return 100.0
            elif distance < radius * 1.5:
                return 50.0
        
        return 0.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATS UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_player_stats(
        self,
        player_id: str,
        player_name: str,
        event: UtilityEvent,
    ) -> None:
        """Update player utility statistics"""
        if player_id not in self.player_stats:
            self.player_stats[player_id] = PlayerUtilityStats(
                player_id=player_id,
                player_name=player_name,
            )
        
        stats = self.player_stats[player_id]
        utype = event.utility_type
        
        if utype == UtilityType.SMOKE:
            stats.smokes_used += 1
        elif utype == UtilityType.GRENADE:
            stats.grenades_used += 1
            if event.result == UtilityResult.HIT:
                stats.grenade_hits += 1
        elif utype == UtilityType.MOLOTOV:
            stats.molotovs_used += 1
            if event.result == UtilityResult.HIT:
                stats.molotov_hits += 1
        elif utype == UtilityType.FLASH:
            stats.flashes_used += 1
            if event.result == UtilityResult.HIT:
                stats.flash_hits += 1
        elif utype == UtilityType.STUN:
            stats.stuns_used += 1
        elif utype in [UtilityType.FIRST_AID, UtilityType.MEDKIT, UtilityType.BANDAGE]:
            stats.healing_done += event.healing_amount
        elif utype == UtilityType.BOOST:
            stats.boosts_used += 1
        
        # Update per-minute averages
        match_duration = (time.time() - self.match_start_time) / 60
        if match_duration > 0:
            total_used = (
                stats.smokes_used + stats.grenades_used + 
                stats.molotovs_used + stats.flashes_used
            )
            stats.avg_utility_use_per_minute = total_used / match_duration
    
    def _update_team_stats(self, event: UtilityEvent) -> None:
        """Update team utility statistics"""
        utype = event.utility_type
        
        if utype == UtilityType.SMOKE:
            self.team_stats.total_smokes += 1
        elif utype == UtilityType.GRENADE:
            self.team_stats.total_grenades += 1
        elif utype == UtilityType.MOLOTOV:
            self.team_stats.total_molotovs += 1
        elif utype == UtilityType.FLASH:
            self.team_stats.total_flashes += 1
        elif utype == UtilityType.STUN:
            self.team_stats.total_stuns += 1
        elif utype in [UtilityType.FIRST_AID, UtilityType.MEDKIT, UtilityType.BANDAGE]:
            self.team_stats.total_healing += event.healing_amount
        elif utype == UtilityType.BOOST:
            self.team_stats.total_boosts += 1
        
        # Update effectiveness ratios
        total_grenades = self.team_stats.total_grenades
        if total_grenades > 0:
            total_hits = sum(
                s.grenade_hits for s in self.player_stats.values()
            )
            self.team_stats.grenade_accuracy = (total_hits / total_grenades) * 100
        
        total_molotovs = self.team_stats.total_molotovs
        if total_molotovs > 0:
            total_hits = sum(
                s.molotov_hits for s in self.player_stats.values()
            )
            self.team_stats.molotov_accuracy = (total_hits / total_molotovs) * 100
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ANALYSIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_utility_summary(self) -> Dict[str, Any]:
        """Get utility usage summary"""
        return {
            "team": {
                "total_smokes": self.team_stats.total_smokes,
                "total_grenades": self.team_stats.total_grenades,
                "total_molotovs": self.team_stats.total_molotovs,
                "total_flashes": self.team_stats.total_flashes,
                "total_stuns": self.team_stats.total_stuns,
                "total_healing": self.team_stats.total_healing,
                "total_boosts": self.team_stats.total_boosts,
                "grenade_accuracy": round(self.team_stats.grenade_accuracy, 1),
                "molotov_accuracy": round(self.team_stats.molotov_accuracy, 1),
            },
            "inventory": self.get_team_inventory_total(),
            "active_smokes": len(self.active_smokes),
            "players": {
                pid: {
                    "name": s.player_name,
                    "smokes": s.smokes_used,
                    "grenades": s.grenades_used,
                    "grenade_hits": s.grenade_hits,
                    "grenade_kills": s.grenade_kills,
                    "molotovs": s.molotovs_used,
                    "flashes": s.flashes_used,
                    "stuns": s.stuns_used,
                    "healing": s.healing_done,
                    "boosts": s.boosts_used,
                    "avg_per_minute": round(s.avg_utility_use_per_minute, 2),
                }
                for pid, s in self.player_stats.items()
            },
        }
    
    def get_utility_events_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent utility events"""
        events = list(self.utility_events)[-limit:]
        return [
            {
                "event_id": e.event_id,
                "player_name": e.player_name,
                "type": e.utility_type.value,
                "result": e.result.value,
                "damage": e.damage_dealt,
                "hits": e.enemies_hit,
                "timestamp": e.timestamp,
            }
            for e in events
        ]
    
    def reset_for_new_match(self) -> None:
        """Reset tracker for new match"""
        self.utility_events.clear()
        self.player_stats.clear()
        self.team_stats = TeamUtilityStats(team_id=self.team_id)
        self.active_smokes.clear()
        self.active_heals.clear()
        self.player_inventories.clear()
        self.match_start_time = time.time()
        self._log.info("Utility tracker reset for new match")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
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
