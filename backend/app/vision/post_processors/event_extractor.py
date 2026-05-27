# backend/app/vision/post_processors/event_extractor.py
"""
AEGIS AI - Event Extractor
Converts raw vision detections into structured game events.
Transforms OCR text, bounding boxes, and detection results
into actionable game state events for the agent system.
"""

import time
import uuid
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.vision.events")


class EventType(str, Enum):
    """Types of extracted game events"""
    # Kill feed events
    KILL = "kill"
    KNOCK = "knock"
    TEAM_WIPE = "team_wipe"
    FIRST_BLOOD = "first_blood"
    
    # Player state events
    HP_CHANGE = "hp_change"
    ARMOR_CHANGE = "armor_change"
    PLAYER_KNOCKED = "player_knocked"
    PLAYER_REVIVED = "player_revived"
    PLAYER_DIED = "player_died"
    PLAYER_HEALING = "player_healing"
    PLAYER_BOOSTING = "player_boosting"
    
    # Position events
    POSITION_UPDATE = "position_update"
    TEAM_POSITION_UPDATE = "team_position_update"
    ZONE_CHANGE = "zone_change"
    
    # Combat events
    WEAPON_FIRE = "weapon_fire"
    WEAPON_RELOAD = "weapon_reload"
    GRENADE_THROW = "grenade_throw"
    SMOKE_DEPLOY = "smoke_deploy"
    MOLOTOV_THROW = "molotov_throw"
    EXPLOSION = "explosion"
    
    # Vehicle events
    VEHICLE_ENTER = "vehicle_enter"
    VEHICLE_EXIT = "vehicle_exit"
    VEHICLE_DESTROY = "vehicle_destroy"
    
    # Zone events
    ZONE_SHRINK_START = "zone_shrink_start"
    ZONE_SHRINK_COMPLETE = "zone_shrink_complete"
    BLUE_ZONE_DAMAGE = "blue_zone_damage"
    
    # Loot events
    LOOT_PICKUP = "loot_pickup"
    AIRDROP_LAND = "airdrop_land"
    DEATH_BOX_SPAWN = "death_box_spawn"
    DEATH_BOX_LOOTED = "death_box_looted"
    
    # Match events
    MATCH_START = "match_start"
    MATCH_END = "match_end"
    PLANE_PATH = "plane_path"
    FINAL_CIRCLE = "final_circle"
    
    # System events
    FEED_HEALTH_ALERT = "feed_health_alert"
    DETECTION_CONFIDENCE_LOW = "detection_confidence_low"


@dataclass
class GameEvent:
    """Structured game event extracted from vision detections"""
    event_type: EventType
    event_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    timestamp: float = field(default_factory=time.time)
    source: str = "vision"  # "ocr", "detection", "minimap", etc.
    source_id: str = ""  # Feed identifier
    
    # Participants
    player_id: Optional[str] = None
    player_name: Optional[str] = None
    team_id: Optional[str] = None
    team_name: Optional[str] = None
    
    # Secondary participants
    target_id: Optional[str] = None
    target_name: Optional[str] = None
    target_team_id: Optional[str] = None
    target_team_name: Optional[str] = None
    
    # Event data
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Location
    screen_x: Optional[float] = None
    screen_y: Optional[float] = None
    world_x: Optional[float] = None
    world_y: Optional[float] = None
    
    # Confidence
    detection_confidence: float = 0.0
    extraction_confidence: float = 0.0
    
    # Validation
    is_valid: bool = True
    validation_errors: List[str] = field(default_factory=list)
    
    # Processing
    processed: bool = False
    processed_at: Optional[float] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for event bus"""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "source": self.source,
            "player_id": self.player_id,
            "player_name": self.player_name,
            "team_id": self.team_id,
            "team_name": self.team_name,
            "target_id": self.target_id,
            "target_name": self.target_name,
            "target_team_id": self.target_team_id,
            "data": self.data,
            "world_position": (
                (round(self.world_x, 1), round(self.world_y, 1))
                if self.world_x is not None and self.world_y is not None
                else None
            ),
            "confidence": round(self.extraction_confidence, 3),
        }


class EventExtractor:
    """
    Converts raw vision detections into structured game events.
    
    Transforms:
    - OCR kill feed text → Kill/Knock events
    - HP bar detections → HP change events
    - Minimap readings → Position/Zone events
    - Utility detections → Grenade/Smoke events
    - Vehicle detections → Vehicle events
    
    Features:
    - Event deduplication
    - Cross-source validation
    - Confidence scoring
    - Event enrichment (adding player/team context)
    - Temporal filtering
    """
    
    def __init__(self):
        # Recent events for deduplication
        self.recent_events: deque = deque(maxlen=200)
        
        # Event counters
        self._event_counts: Dict[EventType, int] = {}
        
        # Known player/team mappings
        self.player_team_map: Dict[str, str] = {}
        self.team_name_map: Dict[str, str] = {}
        
        # Validation rules
        self.validation_rules = {
            EventType.KILL: self._validate_kill_event,
            EventType.KNOCK: self._validate_knock_event,
            EventType.HP_CHANGE: self._validate_hp_event,
            EventType.POSITION_UPDATE: self._validate_position_event,
            EventType.ZONE_CHANGE: self._validate_zone_event,
        }
        
        self._log = logger.bind(component="event_extractor")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT EXTRACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def extract_kill_event(
        self,
        kill_feed_entry: Dict[str, Any],
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract kill/knock event from kill feed OCR"""
        
        killer_name = kill_feed_entry.get("killer_name", "")
        victim_name = kill_feed_entry.get("victim_name", "")
        weapon = kill_feed_entry.get("weapon", "")
        headshot = kill_feed_entry.get("headshot", False)
        kill_type = kill_feed_entry.get("kill_type", "kill")
        confidence = kill_feed_entry.get("confidence", 0.5)
        
        if not killer_name or not victim_name:
            return None
        
        # Determine event type
        event_type = EventType.KILL if kill_type == "kill" else EventType.KNOCK
        
        # Check for team wipe
        is_team_wipe = kill_feed_entry.get("is_team_wipe", False)
        
        # Look up team info
        killer_team = self.player_team_map.get(killer_name, "")
        victim_team = self.player_team_map.get(victim_name, "")
        
        event = GameEvent(
            event_type=event_type,
            source="ocr",
            source_id=source_id,
            player_name=killer_name,
            team_id=killer_team,
            target_name=victim_name,
            target_team_id=victim_team,
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.9,
            data={
                "weapon": weapon,
                "headshot": headshot,
                "is_team_wipe": is_team_wipe,
                "kill_type": kill_type,
            },
        )
        
        # Validate
        event = self._validate_event(event)
        
        if event and event.is_valid:
            self._record_event(event)
            
            # Also emit team wipe event
            if is_team_wipe:
                self.extract_team_wipe_event(victim_team, killer_team, source_id)
        
        return event
    
    def extract_hp_event(
        self,
        hp_info: Dict[str, Any],
        previous_hp: Optional[Dict[str, Any]] = None,
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract HP change event from HP bar detection"""
        
        player_name = hp_info.get("player_name", "")
        player_id = hp_info.get("player_id", "")
        hp_current = hp_info.get("hp_current", 100)
        hp_percentage = hp_info.get("hp_percentage", 100.0)
        armor = hp_info.get("armor_current", 100)
        is_knocked = hp_info.get("is_knocked", False)
        confidence = hp_info.get("confidence", 0.5)
        
        if not player_id and not player_name:
            return None
        
        team_id = self.player_team_map.get(player_name, "")
        
        # Check for HP change
        hp_delta = 0
        if previous_hp:
            prev_hp = previous_hp.get("hp_current", hp_current)
            hp_delta = hp_current - prev_hp
        
        event = GameEvent(
            event_type=EventType.HP_CHANGE,
            source="detection",
            source_id=source_id,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.85,
            data={
                "hp_current": hp_current,
                "hp_percentage": round(hp_percentage, 1),
                "hp_delta": hp_delta,
                "armor": armor,
                "is_knocked": is_knocked,
            },
        )
        
        event = self._validate_event(event)
        
        if event and event.is_valid and abs(hp_delta) > 0:
            self._record_event(event)
            
            # Also emit knock event if player just got knocked
            if is_knocked and previous_hp and not previous_hp.get("is_knocked", False):
                self.extract_knock_event(player_id, player_name, team_id, source_id)
        
        return event
    
    def extract_position_event(
        self,
        position_data: Dict[str, Any],
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract position update from minimap/player tracker"""
        
        player_id = position_data.get("player_id", "")
        player_name = position_data.get("player_name", "")
        world_x = position_data.get("world_x", 0.0)
        world_y = position_data.get("world_y", 0.0)
        screen_x = position_data.get("screen_x", 0.0)
        screen_y = position_data.get("screen_y", 0.0)
        speed = position_data.get("speed", 0.0)
        direction = position_data.get("direction", 0.0)
        confidence = position_data.get("confidence", 0.5)
        
        team_id = self.player_team_map.get(player_name, "")
        
        event = GameEvent(
            event_type=EventType.POSITION_UPDATE,
            source="minimap" if world_x > 0 else "tracking",
            source_id=source_id,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            screen_x=screen_x,
            screen_y=screen_y,
            world_x=world_x,
            world_y=world_y,
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.9,
            data={
                "speed": round(speed, 1),
                "direction": round(direction, 1),
                "is_moving": speed > 5,
            },
        )
        
        return self._validate_event(event)
    
    def extract_zone_event(
        self,
        zone_data: Dict[str, Any],
        previous_zone: Optional[Dict[str, Any]] = None,
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract zone change event"""
        
        zone_phase = zone_data.get("zone_phase", 0)
        safe_x = zone_data.get("safe_zone_center_x", 0.0)
        safe_y = zone_data.get("safe_zone_center_y", 0.0)
        safe_radius = zone_data.get("safe_zone_radius", 0.0)
        is_shrinking = zone_data.get("is_shrinking", False)
        timer = zone_data.get("zone_shrink_timer", 0.0)
        confidence = zone_data.get("confidence", 0.5)
        
        # Check if zone changed
        phase_changed = previous_zone and previous_zone.get("zone_phase", 0) != zone_phase
        
        if not phase_changed and not is_shrinking and previous_zone:
            return None
        
        event = GameEvent(
            event_type=EventType.ZONE_CHANGE,
            source="minimap",
            source_id=source_id,
            world_x=safe_x,
            world_y=safe_y,
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.85,
            data={
                "zone_phase": zone_phase,
                "safe_zone_center": (round(safe_x, 1), round(safe_y, 1)),
                "safe_zone_radius": round(safe_radius, 1),
                "is_shrinking": is_shrinking,
                "shrink_timer": round(timer, 1),
                "phase_changed": phase_changed,
            },
        )
        
        event = self._validate_event(event)
        
        if event and event.is_valid:
            self._record_event(event)
            
            # Emit specific zone events
            if is_shrinking and phase_changed:
                self._emit_zone_shrink_start(event, source_id)
        
        return event
    
    def extract_utility_event(
        self,
        utility_data: Dict[str, Any],
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract utility usage event"""
        
        utility_type = utility_data.get("utility_type", "")
        position = utility_data.get("landing_position", (0, 0))
        player_name = utility_data.get("player_name", "")
        confidence = utility_data.get("confidence", 0.5)
        
        event_type_map = {
            "grenade": EventType.GRENADE_THROW,
            "smoke": EventType.SMOKE_DEPLOY,
            "molotov": EventType.MOLOTOV_THROW,
        }
        
        event_type = event_type_map.get(utility_type)
        if not event_type:
            return None
        
        team_id = self.player_team_map.get(player_name, "")
        
        event = GameEvent(
            event_type=event_type,
            source="detection",
            source_id=source_id,
            player_name=player_name,
            team_id=team_id,
            world_x=position[0] if position else 0,
            world_y=position[1] if position else 0,
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.8,
            data={
                "utility_type": utility_type,
                "position": position,
                "affected_players": utility_data.get("affected_players", []),
                "damage": utility_data.get("damage_dealt", 0),
            },
        )
        
        return self._validate_event(event)
    
    def extract_vehicle_event(
        self,
        vehicle_data: Dict[str, Any],
        event_subtype: str = "enter",
        source_id: str = "",
    ) -> Optional[GameEvent]:
        """Extract vehicle interaction event"""
        
        vehicle_type = vehicle_data.get("vehicle_type", "")
        player_name = vehicle_data.get("driver_id", "")
        position = (vehicle_data.get("position_x", 0), vehicle_data.get("position_y", 0))
        confidence = vehicle_data.get("confidence", 0.5)
        
        event_type_map = {
            "enter": EventType.VEHICLE_ENTER,
            "exit": EventType.VEHICLE_EXIT,
            "destroy": EventType.VEHICLE_DESTROY,
        }
        
        event_type = event_type_map.get(event_subtype)
        if not event_type:
            return None
        
        team_id = self.player_team_map.get(player_name, "")
        
        event = GameEvent(
            event_type=event_type,
            source="detection",
            source_id=source_id,
            player_name=player_name,
            team_id=team_id,
            world_x=position[0],
            world_y=position[1],
            detection_confidence=confidence,
            extraction_confidence=confidence * 0.8,
            data={
                "vehicle_type": vehicle_type,
                "position": position,
                "speed": vehicle_data.get("speed", 0),
                "occupants": vehicle_data.get("occupant_count", 0),
            },
        )
        
        return self._validate_event(event)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SPECIALIZED EVENTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def extract_team_wipe_event(
        self,
        eliminated_team: str,
        eliminator_team: str,
        source_id: str = "",
    ) -> GameEvent:
        """Create team wipe event"""
        
        event = GameEvent(
            event_type=EventType.TEAM_WIPE,
            source="ocr",
            source_id=source_id,
            team_id=eliminated_team,
            team_name=self.team_name_map.get(eliminated_team, eliminated_team),
            target_team_id=eliminator_team,
            target_team_name=self.team_name_map.get(eliminator_team, eliminator_team),
            detection_confidence=0.8,
            extraction_confidence=0.9,
            data={
                "eliminated_team": eliminated_team,
                "eliminator_team": eliminator_team,
            },
        )
        
        self._record_event(event)
        return event
    
    def extract_knock_event(
        self,
        player_id: str,
        player_name: str,
        team_id: str,
        source_id: str = "",
    ) -> GameEvent:
        """Create player knocked event"""
        
        event = GameEvent(
            event_type=EventType.PLAYER_KNOCKED,
            source="detection",
            source_id=source_id,
            player_id=player_id,
            player_name=player_name,
            team_id=team_id,
            detection_confidence=0.75,
            extraction_confidence=0.85,
        )
        
        self._record_event(event)
        return event
    
    def _emit_zone_shrink_start(self, zone_event: GameEvent, source_id: str) -> None:
        """Emit zone shrink start event"""
        
        event = GameEvent(
            event_type=EventType.ZONE_SHRINK_START,
            source="minimap",
            source_id=source_id,
            world_x=zone_event.world_x,
            world_y=zone_event.world_y,
            detection_confidence=zone_event.detection_confidence,
            extraction_confidence=0.9,
            data=zone_event.data,
        )
        
        self._record_event(event)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VALIDATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _validate_event(self, event: GameEvent) -> GameEvent:
        """Validate an extracted event"""
        
        validator = self.validation_rules.get(event.event_type)
        
        if validator:
            event = validator(event)
        
        # General validation
        if event.detection_confidence < 0.3:
            event.is_valid = False
            event.validation_errors.append("Low detection confidence")
        
        if event.extraction_confidence < 0.2:
            event.is_valid = False
            event.validation_errors.append("Low extraction confidence")
        
        return event
    
    def _validate_kill_event(self, event: GameEvent) -> GameEvent:
        """Validate kill event"""
        
        if not event.player_name or not event.target_name:
            event.is_valid = False
            event.validation_errors.append("Missing player names")
        
        if event.player_name == event.target_name:
            event.is_valid = False
            event.validation_errors.append("Self-kill detected")
        
        return event
    
    def _validate_knock_event(self, event: GameEvent) -> GameEvent:
        """Validate knock event"""
        return self._validate_kill_event(event)
    
    def _validate_hp_event(self, event: GameEvent) -> GameEvent:
        """Validate HP change event"""
        
        hp = event.data.get("hp_current", -1)
        if hp < 0 or hp > 100:
            event.is_valid = False
            event.validation_errors.append("Invalid HP value")
        
        return event
    
    def _validate_position_event(self, event: GameEvent) -> GameEvent:
        """Validate position event"""
        
        if event.world_x is not None and event.world_y is not None:
            if event.world_x < -1000 or event.world_x > 9000:
                event.is_valid = False
                event.validation_errors.append("Position out of map bounds")
        
        return event
    
    def _validate_zone_event(self, event: GameEvent) -> GameEvent:
        """Validate zone event"""
        
        radius = event.data.get("safe_zone_radius", 0)
        if radius <= 0:
            event.is_valid = False
            event.validation_errors.append("Invalid zone radius")
        
        return event
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_player_team_map(self, mapping: Dict[str, str]) -> None:
        """Set player name to team mapping"""
        self.player_team_map.update(mapping)
    
    def set_team_names(self, mapping: Dict[str, str]) -> None:
        """Set team ID to team name mapping"""
        self.team_name_map.update(mapping)
    
    def _record_event(self, event: GameEvent) -> None:
        """Record event for deduplication"""
        self.recent_events.append(event)
        
        # Update counter
        self._event_counts[event.event_type] = self._event_counts.get(event.event_type, 0) + 1
    
    def is_duplicate(self, event: GameEvent, window_seconds: float = 2.0) -> bool:
        """Check if event is a duplicate of a recent event"""
        
        current_time = time.time()
        
        for recent in self.recent_events:
            if current_time - recent.timestamp > window_seconds:
                continue
            
            # Same type, same primary player
            if (event.event_type == recent.event_type and
                event.player_name == recent.player_name and
                event.target_name == recent.target_name):
                return True
        
        return False
    
    def get_recent_events(self, event_type: Optional[EventType] = None, limit: int = 20) -> List[GameEvent]:
        """Get recent extracted events"""
        
        events = list(self.recent_events)
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events[-limit:]
    
    def get_event_counts(self) -> Dict[str, int]:
        """Get event type counts"""
        return {
            event_type.value: count
            for event_type, count in self._event_counts.items()
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get event extractor statistics"""
        return {
            "total_events_extracted": len(self.recent_events),
            "event_counts": self.get_event_counts(),
            "recent_events": [
                {
                    "type": e.event_type.value,
                    "player": e.player_name,
                    "target": e.target_name,
                    "timestamp": e.timestamp,
                }
                for e in list(self.recent_events)[-10:]
            ],
        }
    
    def reset(self) -> None:
        """Reset event extractor"""
        self.recent_events.clear()
        self._event_counts.clear()
        self._log.info("Event extractor reset")