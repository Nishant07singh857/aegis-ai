# backend/app/agents/referee_agents/rule_enforcer.py
"""
AEGIS AI - Rule Enforcer
Automated tournament rule checking and violation detection.
Monitors matches for rule compliance in real-time.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque, defaultdict
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.rule_enforcer")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ViolationSeverity(str, Enum):
    """Severity levels for rule violations"""
    CRITICAL = "critical"         # Immediate disqualification
    MAJOR = "major"              # Point deduction, possible DQ
    MINOR = "minor"              # Warning, small penalty
    WARNING = "warning"          # First warning
    TECHNICAL = "technical"      # Technical rule breach

class ViolationType(str, Enum):
    """Types of rule violations"""
    TEAMING = "teaming"                      # Collusion between teams
    EXPLOIT_ABUSE = "exploit_abuse"          # Using game exploits
    STREAM_SNIPING = "stream_sniping"        # Watching opponent streams
    INAPPROPRIATE_BEHAVIOR = "inappropriate_behavior"  # Toxic behavior
    RINGING = "ringing"                     # Playing under someone else's account
    MACRO_USAGE = "macro_usage"             # Using macros/scripts
    VPN_ABUSE = "vpn_abuse"                 # Abusing VPN for advantage
    INTENTIONAL_FEEDING = "intentional_feeding"  # Deliberately dying
    GRIEFING = "griefing"                   # Sabotaging teammates
    OUTSIDE_COMMUNICATION = "outside_communication"  # External comms during match
    DEVICE_TAMPERING = "device_tampering"    # Modifying device
    SPECTATOR_ABUSE = "spectator_abuse"      # Abusing spectator features
    DELAY_ABUSE = "delay_abuse"             # Intentional delays
    NAME_VIOLATION = "name_violation"        # Inappropriate names
    ROSTER_VIOLATION = "roster_violation"    # Unauthorized roster changes
    MAP_EXPLOIT = "map_exploit"             # Glitching through map

class EnforcementAction(str, Enum):
    """Actions taken for violations"""
    WARNING = "warning"
    POINT_DEDUCTION = "point_deduction"
    MATCH_FORFEIT = "match_forfeit"
    TOURNAMENT_DQ = "tournament_dq"
    TEMP_BAN = "temp_ban"
    PERM_BAN = "perm_ban"
    REPLAY_MATCH = "replay_match"
    NO_ACTION = "no_action"

@dataclass
class RuleViolation:
    """A detected rule violation"""
    violation_id: str
    violation_type: ViolationType
    severity: ViolationSeverity = ViolationSeverity.MINOR
    
    # Who
    team_id: str = ""
    team_name: str = ""
    player_id: str = ""
    player_name: str = ""
    
    # What happened
    description: str = ""
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    
    # When
    detected_at: float = field(default_factory=time.time)
    match_id: str = ""
    match_time_ms: float = 0.0
    
    # Action
    recommended_action: EnforcementAction = EnforcementAction.WARNING
    action_taken: Optional[EnforcementAction] = None
    action_reason: str = ""
    
    # Penalty
    points_deducted: int = 0
    penalty_description: str = ""
    
    # Status
    is_resolved: bool = False
    resolved_at: Optional[float] = None
    resolved_by: str = ""
    
    # Appeal
    can_appeal: bool = True
    appeal_deadline: Optional[float] = None
    appealed: bool = False
    
    # History
    previous_violations: int = 0  # Previous violations by same team/player

@dataclass
class TournamentRules:
    """Tournament rule configuration"""
    tournament_id: str
    tournament_name: str
    
    # Point system
    points_per_kill: int = 1
    placement_points: Dict[int, int] = field(default_factory=dict)
    
    # Match rules
    max_team_size: int = 4
    min_team_size: int = 3
    max_teams: int = 16
    
    # Violation penalties
    penalty_points: Dict[ViolationSeverity, int] = field(default_factory=lambda: {
        ViolationSeverity.CRITICAL: -100,
        ViolationSeverity.MAJOR: -50,
        ViolationSeverity.MINOR: -10,
        ViolationSeverity.WARNING: 0,
        ViolationSeverity.TECHNICAL: -5,
    })
    
    # Violation thresholds (before escalation)
    max_warnings: int = 2
    max_minor_violations: int = 3
    max_major_violations: int = 1
    
    # Specific rules
    teaming_distance_threshold: float = 100.0  # Meters - teams closer than this flagged
    teaming_time_threshold: float = 30.0       # Seconds - teams together this long flagged
    max_ping_threshold: float = 150.0          # Max allowed ping
    afk_timeout: float = 120.0                 # Seconds before AFK flag
    
    # Enable/disable checks
    check_teaming: bool = True
    check_exploits: bool = True
    check_afk: bool = True
    check_names: bool = True


class RuleEnforcer(BaseAgent):
    """
    Automated tournament rule enforcement.
    
    Monitors for:
    - Teaming/collusion between teams
    - Game exploit usage
    - Stream sniping indicators
    - AFK/inactive players
    - Inappropriate behavior
    - Roster violations
    - Technical violations
    
    Takes automatic actions:
    - Warnings
    - Point deductions
    - Match forfeits
    - Tournament disqualification
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Active violations
        self.active_violations: Dict[str, RuleViolation] = {}
        self.violation_history: deque = deque(maxlen=500)
        
        # Team violation tracking
        self.team_violations: Dict[str, List[str]] = defaultdict(list)
        self.player_violations: Dict[str, List[str]] = defaultdict(list)
        
        # Monitoring state
        self.team_proximity_tracking: Dict[str, Dict[str, float]] = defaultdict(dict)
        self.player_activity_tracking: Dict[str, float] = {}
        
        # Current rules
        self.rules: Optional[TournamentRules] = None
        
        # Escalation logic
        self.escalation_map = {
            ViolationSeverity.WARNING: {
                "repeat_threshold": 3,
                "escalate_to": ViolationSeverity.MINOR,
            },
            ViolationSeverity.MINOR: {
                "repeat_threshold": 3,
                "escalate_to": ViolationSeverity.MAJOR,
            },
            ViolationSeverity.MAJOR: {
                "repeat_threshold": 2,
                "escalate_to": ViolationSeverity.CRITICAL,
            },
        }
        
        self._log = logger.bind(component="rule_enforcer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RULE CHECKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_rules(self, rules: TournamentRules) -> None:
        """Set tournament rules"""
        self.rules = rules
        self._log.info(f"Rules set for tournament: {rules.tournament_name}")
    
    def check_violation(
        self,
        violation_type: ViolationType,
        event_data: Dict[str, Any],
        match_context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check if an event constitutes a rule violation"""
        
        if not self.rules:
            return None
        
        violation = None
        
        if violation_type == ViolationType.TEAMING:
            violation = self._check_teaming(event_data, match_context)
        elif violation_type == ViolationType.EXPLOIT_ABUSE:
            violation = self._check_exploit(event_data, match_context)
        elif violation_type == ViolationType.INTENTIONAL_FEEDING:
            violation = self._check_feeding(event_data, match_context)
        elif violation_type == ViolationType.AFK:
            violation = self._check_afk(event_data, match_context)
        elif violation_type == ViolationType.MAP_EXPLOIT:
            violation = self._check_map_exploit(event_data, match_context)
        elif violation_type == ViolationType.DEVICE_TAMPERING:
            violation = self._check_device_tampering(event_data, match_context)
        
        if violation:
            self._process_violation(violation)
        
        return violation
    
    def _check_teaming(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for teaming between teams"""
        
        if not self.rules or not self.rules.check_teaming:
            return None
        
        team_a = data.get("team_id", "")
        team_b = data.get("other_team_id", "")
        distance = data.get("distance_between_teams", 999)
        time_together = data.get("time_together_seconds", 0)
        
        # Check if teams are too close for too long
        if (distance < self.rules.teaming_distance_threshold and 
            time_together > self.rules.teaming_time_threshold):
            
            # Check if they're fighting (fighting is allowed)
            if data.get("is_fighting", False):
                return None
            
            severity = ViolationSeverity.MAJOR
            
            # If it's a repeated offense, escalate
            team_violations = self.team_violations.get(team_a, [])
            teaming_count = sum(
                1 for v_id in team_violations
                if v_id in self.violation_history and 
                hasattr(self.violation_history, 'get') and
                self.active_violations.get(v_id, RuleViolation(violation_id="")).violation_type == ViolationType.TEAMING
            )
            
            if teaming_count >= 1:
                severity = ViolationSeverity.CRITICAL
            
            return RuleViolation(
                violation_id=f"team_{int(time.time())}_{team_a[:8]}_{team_b[:8]}",
                violation_type=ViolationType.TEAMING,
                severity=severity,
                team_id=team_a,
                team_name=data.get("team_name", ""),
                description=f"Possible teaming detected: {data.get('team_name', 'Team')} and {data.get('other_team_name', 'Team')} within {distance:.0f}m for {time_together:.0f}s without engaging",
                match_id=context.get("match_id", ""),
                match_time_ms=context.get("match_time_ms", 0),
                recommended_action=EnforcementAction.POINT_DEDUCTION if severity == ViolationSeverity.MAJOR else EnforcementAction.TOURNAMENT_DQ,
                points_deducted=self.rules.penalty_points.get(severity, 20),
                previous_violations=teaming_count,
            )
        
        return None
    
    def _check_exploit(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for game exploit usage"""
        
        if not self.rules or not self.rules.check_exploits:
            return None
        
        exploit_type = data.get("exploit_type", "")
        location = data.get("location", {})
        
        # Known exploit locations or behaviors
        known_exploits = [
            "under_map", "inside_rock", "invisible_spot",
            "wall_breach", "roof_glitch", "infinite_boost",
        ]
        
        if exploit_type in known_exploits:
            return RuleViolation(
                violation_id=f"exploit_{int(time.time())}_{data.get('player_id', 'unknown')[:8]}",
                violation_type=ViolationType.EXPLOIT_ABUSE,
                severity=ViolationSeverity.MAJOR,
                player_id=data.get("player_id", ""),
                player_name=data.get("player_name", ""),
                team_id=data.get("team_id", ""),
                team_name=data.get("team_name", ""),
                description=f"Player {data.get('player_name', 'Unknown')} detected using {exploit_type} exploit at {location}",
                match_id=context.get("match_id", ""),
                match_time_ms=context.get("match_time_ms", 0),
                recommended_action=EnforcementAction.POINT_DEDUCTION,
                points_deducted=self.rules.penalty_points.get(ViolationSeverity.MAJOR, 50),
            )
        
        return None
    
    def _check_feeding(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for intentional feeding/griefing"""
        
        deaths = data.get("deaths_short_time", 0)
        damage_to_enemies = data.get("damage_to_enemies", 0)
        
        # Multiple deaths with no damage = suspicious
        if deaths >= 3 and damage_to_enemies < 50:
            return RuleViolation(
                violation_id=f"feed_{int(time.time())}_{data.get('player_id', 'unknown')[:8]}",
                violation_type=ViolationType.INTENTIONAL_FEEDING,
                severity=ViolationSeverity.MINOR,
                player_id=data.get("player_id", ""),
                player_name=data.get("player_name", ""),
                team_id=data.get("team_id", ""),
                description=f"Possible intentional feeding: {deaths} deaths with only {damage_to_enemies} damage dealt",
                match_id=context.get("match_id", ""),
                recommended_action=EnforcementAction.WARNING,
                points_deducted=self.rules.penalty_points.get(ViolationSeverity.MINOR, 10),
            )
        
        return None
    
    def _check_afk(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for AFK/inactive players"""
        
        if not self.rules or not self.rules.check_afk:
            return None
        
        inactivity_time = data.get("inactivity_seconds", 0)
        
        if inactivity_time > self.rules.afk_timeout:
            return RuleViolation(
                violation_id=f"afk_{int(time.time())}_{data.get('player_id', 'unknown')[:8]}",
                violation_type=ViolationType.DELAY_ABUSE,
                severity=ViolationSeverity.TECHNICAL,
                player_id=data.get("player_id", ""),
                player_name=data.get("player_name", ""),
                team_id=data.get("team_id", ""),
                description=f"Player {data.get('player_name', 'Unknown')} inactive for {inactivity_time:.0f}s",
                match_id=context.get("match_id", ""),
                recommended_action=EnforcementAction.WARNING,
            )
        
        return None
    
    def _check_map_exploit(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for map exploits (out of bounds, etc.)"""
        
        position = data.get("position", {})
        map_bounds = data.get("map_bounds", {})
        
        x = position.get("x", 0)
        y = position.get("y", 0)
        
        # Check if position is outside playable area
        min_x = map_bounds.get("min_x", -100)
        max_x = map_bounds.get("max_x", 8100)
        min_y = map_bounds.get("min_y", -100)
        max_y = map_bounds.get("max_y", 8100)
        
        if x < min_x or x > max_x or y < min_y or y > max_y:
            return RuleViolation(
                violation_id=f"map_exploit_{int(time.time())}",
                violation_type=ViolationType.MAP_EXPLOIT,
                severity=ViolationSeverity.MAJOR,
                player_id=data.get("player_id", ""),
                player_name=data.get("player_name", ""),
                description=f"Player outside map bounds: ({x:.0f}, {y:.0f})",
                match_id=context.get("match_id", ""),
                recommended_action=EnforcementAction.POINT_DEDUCTION,
                points_deducted=self.rules.penalty_points.get(ViolationSeverity.MAJOR, 50),
            )
        
        return None
    
    def _check_device_tampering(
        self,
        data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Optional[RuleViolation]:
        """Check for device tampering"""
        
        tampering_type = data.get("tampering_type", "")
        
        if tampering_type:
            return RuleViolation(
                violation_id=f"device_{int(time.time())}",
                violation_type=ViolationType.DEVICE_TAMPERING,
                severity=ViolationSeverity.CRITICAL,
                player_id=data.get("player_id", ""),
                player_name=data.get("player_name", ""),
                team_id=data.get("team_id", ""),
                description=f"Device tampering detected: {tampering_type}",
                match_id=context.get("match_id", ""),
                recommended_action=EnforcementAction.TOURNAMENT_DQ,
                can_appeal=True,
            )
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VIOLATION PROCESSING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _process_violation(self, violation: RuleViolation) -> None:
        """Process a detected violation"""
        
        # Check for escalation
        self._check_escalation(violation)
        
        # Store violation
        self.active_violations[violation.violation_id] = violation
        
        # Track per team/player
        if violation.team_id:
            self.team_violations[violation.team_id].append(violation.violation_id)
        if violation.player_id:
            self.player_violations[violation.player_id].append(violation.violation_id)
        
        # Set appeal deadline (24 hours from detection)
        violation.appeal_deadline = time.time() + 86400
        
        self._log.warning(
            f"Violation detected: {violation.violation_type.value}",
            severity=violation.severity.value,
            team=violation.team_name,
            player=violation.player_name,
            action=violation.recommended_action.value,
        )
    
    def _check_escalation(self, violation: RuleViolation) -> None:
        """Check if violation should be escalated based on history"""
        
        if not violation.team_id:
            return
        
        # Count previous violations of same type
        previous = [
            v_id for v_id in self.team_violations.get(violation.team_id, [])
            if v_id in self.active_violations
        ]
        
        similar_violations = [
            v_id for v_id in previous
            if self.active_violations.get(v_id, RuleViolation(violation_id="")).violation_type == violation.violation_type
        ]
        
        violation.previous_violations = len(similar_violations)
        
        # Check escalation rules
        escalation = self.escalation_map.get(violation.severity)
        if escalation and len(similar_violations) >= escalation["repeat_threshold"]:
            violation.severity = escalation["escalate_to"]
            violation.points_deducted = self.rules.penalty_points.get(
                violation.severity, violation.points_deducted * 2
            ) if self.rules else violation.points_deducted * 2
            
            self._log.warning(
                f"Violation escalated to {violation.severity.value}",
                team=violation.team_name,
                previous_count=len(similar_violations),
            )
    
    def resolve_violation(
        self,
        violation_id: str,
        action: EnforcementAction,
        resolved_by: str = "system",
        reason: str = "",
    ) -> bool:
        """Resolve a violation"""
        
        violation = self.active_violations.get(violation_id)
        if not violation:
            return False
        
        violation.is_resolved = True
        violation.resolved_at = time.time()
        violation.resolved_by = resolved_by
        violation.action_taken = action
        violation.action_reason = reason
        
        # Move to history
        self.violation_history.append(violation)
        del self.active_violations[violation_id]
        
        self._log.info(
            f"Violation resolved: {violation.violation_type.value}",
            action=action.value,
            resolved_by=resolved_by,
        )
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_violations(self) -> List[Dict[str, Any]]:
        """Get all active unresolved violations"""
        return [
            {
                "violation_id": v.violation_id,
                "type": v.violation_type.value,
                "severity": v.severity.value,
                "team_name": v.team_name,
                "player_name": v.player_name,
                "description": v.description,
                "recommended_action": v.recommended_action.value,
                "points_deducted": v.points_deducted,
                "detected_at": v.detected_at,
                "can_appeal": v.can_appeal,
            }
            for v in self.active_violations.values()
        ]
    
    def get_team_violations(self, team_id: str) -> List[Dict[str, Any]]:
        """Get violations for a specific team"""
        violation_ids = self.team_violations.get(team_id, [])
        violations = []
        
        for v_id in violation_ids:
            v = self.active_violations.get(v_id)
            if v:
                violations.append({
                    "type": v.violation_type.value,
                    "severity": v.severity.value,
                    "description": v.description,
                    "action": v.action_taken.value if v.action_taken else "pending",
                    "points_deducted": v.points_deducted,
                    "is_resolved": v.is_resolved,
                })
        
        return violations
    
    def get_violation_summary(self) -> Dict[str, Any]:
        """Get violation summary for tournament"""
        return {
            "active_violations": len(self.active_violations),
            "total_violations": len(self.violation_history) + len(self.active_violations),
            "by_severity": {
                s.value: len([
                    v for v in self.active_violations.values()
                    if v.severity == s
                ])
                for s in ViolationSeverity
            },
            "by_type": {
                t.value: len([
                    v for v in self.active_violations.values()
                    if v.violation_type == t
                ])
                for t in ViolationType
            },
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get rule enforcer statistics"""
        return self.get_violation_summary()
    
    def reset(self) -> None:
        """Reset rule enforcer"""
        self.active_violations.clear()
        self.violation_history.clear()
        self.team_violations.clear()
        self.player_violations.clear()
        self._log.info("Rule enforcer reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
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
