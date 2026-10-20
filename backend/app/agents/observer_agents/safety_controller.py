# backend/app/agents/observer_agents/safety_controller.py
"""
AEGIS AI - Safety Controller
Prevents chaotic broadcast behavior, enforces minimum scene times,
limits switching frequency, and ensures professional production quality.
"""

import time
from typing import Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.safety")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class SafetyRule(str, Enum):
    """Safety rules for broadcast"""
    MIN_SCENE_TIME = "min_scene_time"           # Minimum time on a scene
    MAX_SWITCHES_PER_MINUTE = "max_switches"    # Maximum switches per minute
    NO_SWITCH_DURING_REPLAY = "no_switch_replay" # Don't switch during replay
    CLUTCH_LOCK = "clutch_lock"                 # Lock on clutch until resolved
    FIGHT_LOCK = "fight_lock"                   # Lock on fight for minimum time
    COOLDOWN_PERIOD = "cooldown"               # Cooldown between same team
    NO_RAPID_SWITCHING = "no_rapid_switching"   # Prevent flickering
    REPLAY_SAFETY = "replay_safety"             # Don't interrupt replay
    FINAL_CIRCLE_STABILITY = "final_circle"     # Stable coverage in final circle

class SafetyViolation(str, Enum):
    """Types of safety violations"""
    SCENE_TOO_SHORT = "scene_too_short"
    TOO_MANY_SWITCHES = "too_many_switches"
    CLUTCH_INTERRUPTED = "clutch_interrupted"
    REPLAY_INTERRUPTED = "replay_interrupted"
    RAPID_SWITCHING = "rapid_switching"
    SAME_TEAM_SWITCH = "same_team_switch"
    FINAL_ZONE_INTERRUPTED = "final_zone_interrupted"

@dataclass
class SafetyState:
    """Current safety state"""
    current_scene_started_at: float = 0.0
    current_scene_type: str = ""
    is_clutch: bool = False
    is_replay: bool = False
    is_final_circle: bool = False
    # Phase 22 (W3, M6): teams alive, fed by the director each tick —
    # drives the final-zone HARD lock thresholds (top-5 / top-3).
    teams_alive: int = 16
    current_is_final_zone: bool = False
    
    # Switch tracking
    switch_timestamps: deque = field(default_factory=lambda: deque(maxlen=50))
    recently_shown_teams: deque = field(default_factory=lambda: deque(maxlen=20))
    
    # Lock state
    locked_until: float = 0.0
    lock_reason: str = ""
    
    # Violations
    violations: deque = field(default_factory=lambda: deque(maxlen=50))
    violation_count: int = 0
    
    # Cooldowns
    team_cooldowns: Dict[str, float] = field(default_factory=dict)

def _settings_float(name: str, default: float) -> float:
    try:
        from ...config import settings

        return float(getattr(settings, name, default))
    except Exception:  # pragma: no cover - config unavailable in tools
        return default


@dataclass
class SafetyConfig:
    """Safety configuration.

    Phase 22 (W3, M6) unification: ``min_scene_time`` and
    ``rapid_switch_threshold`` (the ONE named switch cooldown) default
    from the central settings (``MIN_SCENE_TIME_SECONDS`` = 10 s,
    ``SWITCH_COOLDOWN_SECONDS`` = 3 s). Before Phase 22 this class
    carried a divergent private 8.0 s / 2.0 s pair that silently
    disagreed with the director's configured values.
    """
    min_scene_time: float = field(
        default_factory=lambda: _settings_float("MIN_SCENE_TIME_SECONDS", 10.0)
    )
    max_switches_per_minute: int = 8     # Maximum switches per minute
    clutch_lock_time: float = 10.0       # Lock on clutch (seconds)
    fight_lock_time: float = 6.0         # Lock on fight (seconds)
    replay_lock_time: float = 5.0        # Lock during replay
    team_cooldown_time: float = 10.0     # Cooldown between same team
    # The named switch cooldown (PRD 3 s): minimum gap between switches.
    rapid_switch_threshold: float = field(
        default_factory=lambda: _settings_float("SWITCH_COOLDOWN_SECONDS", 3.0)
    )
    final_circle_stability: float = 12.0 # Minimum scene time in final circle


class SafetyController(BaseAgent):
    """
    Prevents chaotic broadcast behavior.
    
    Safety Rules:
    1. Minimum scene time enforcement
    2. Maximum switches per minute limit
    3. Clutch situation lock (don't switch away)
    4. Fight lock (stay on fight for minimum time)
    5. Replay protection (don't interrupt replays)
    6. Team cooldown (don't show same team too often)
    7. Rapid switching prevention
    8. Final circle stability (longer scenes)
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.safety_state = SafetyState()
        self.safety_config = SafetyConfig()
        
        # Emergency override
        self.emergency_override: bool = False
        self.manual_override: bool = False
        
        self._log = logger.bind(component="safety")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAIN CHECKS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def can_switch(self, director_state: Any) -> bool:
        """Check if switching is allowed right now"""
        
        # Manual override bypasses all safety
        if self.manual_override or self.emergency_override:
            return True
        
        # Check if locked
        if time.time() < self.safety_state.locked_until:
            self._log.debug(f"Switch prevented: locked until {self.safety_state.locked_until}")
            return False
        
        # Check minimum scene time
        if self.safety_state.current_scene_started_at > 0:
            scene_duration = time.time() - self.safety_state.current_scene_started_at
            
            # Different minimums for different situations
            min_time = self.safety_config.min_scene_time
            
            if self.safety_state.is_final_circle:
                min_time = self.safety_config.final_circle_stability
            
            if self.safety_state.is_clutch:
                min_time = self.safety_config.clutch_lock_time
            
            if scene_duration < min_time:
                return False
        
        # Check max switches per minute
        recent_switches = [
            t for t in self.safety_state.switch_timestamps
            if time.time() - t < 60
        ]
        if len(recent_switches) >= self.safety_config.max_switches_per_minute:
            self._log.warning("Max switches per minute reached")
            return False
        
        # Check rapid switching
        if self.safety_state.switch_timestamps:
            last_switch = self.safety_state.switch_timestamps[-1]
            if time.time() - last_switch < self.safety_config.rapid_switch_threshold:
                return False
        
        return True
    
    def validate_switch(
        self,
        current_scene: Any,
        new_scene: Any,
    ) -> bool:
        """Validate if a specific switch should be allowed"""
        
        if self.manual_override or self.emergency_override:
            return True
        
        # Don't switch during replay
        if self.safety_state.is_replay and new_scene.feed_type.value != "replay":
            self._record_violation(
                SafetyViolation.REPLAY_INTERRUPTED,
                "Cannot switch during replay",
            )
            return False
        
        # Don't switch away from clutch
        if self.safety_state.is_clutch and not getattr(new_scene, 'is_clutch', False):
            self._record_violation(
                SafetyViolation.CLUTCH_INTERRUPTED,
                "Cannot switch away from clutch",
            )
            return False

        # Phase 22 (W3, M6): final-zone HARD lock — mirrors the clutch
        # lock. In the final circle (top-5, and absolutely at top-3)
        # the camera does not leave a final-zone scene for a lesser
        # one; only another final-zone scene or a clutch may take over.
        if self.safety_state.current_is_final_zone:
            new_is_final = bool(
                getattr(new_scene, 'is_final_circle', False)
                or getattr(new_scene, 'is_final_zone', False)
            )
            new_is_clutch = bool(getattr(new_scene, 'is_clutch', False))
            if not (new_is_final or new_is_clutch):
                if self.safety_state.teams_alive <= 3:
                    self._record_violation(
                        SafetyViolation.FINAL_ZONE_INTERRUPTED,
                        "Final-zone hard lock (top-3): cannot switch away",
                    )
                    return False
                if self.safety_state.teams_alive <= 5:
                    scene_age = time.time() - self.safety_state.current_scene_started_at
                    if scene_age < self.safety_config.final_circle_stability:
                        self._record_violation(
                            SafetyViolation.FINAL_ZONE_INTERRUPTED,
                            "Final-zone hard lock (top-5): scene still locked",
                        )
                        return False
        
        # Don't switch to same team immediately
        if (current_scene and current_scene.team_id and 
            new_scene.team_id and 
            current_scene.team_id == new_scene.team_id):
            
            if current_scene.team_id in self.safety_state.team_cooldowns:
                cooldown_until = self.safety_state.team_cooldowns[current_scene.team_id]
                if time.time() < cooldown_until:
                    self._record_violation(
                        SafetyViolation.SAME_TEAM_SWITCH,
                        "Team cooldown active",
                    )
                    return False
        
        return True
    
    def filter_candidates(
        self,
        candidates: List[Any],
        director_state: Any,
    ) -> List[Any]:
        """Filter out unsafe candidates"""
        safe_candidates = []
        
        for candidate in candidates:
            # Check team cooldown
            if candidate.team_id:
                cooldown_until = self.safety_state.team_cooldowns.get(candidate.team_id, 0)
                if time.time() < cooldown_until:
                    continue  # Skip this team
            
            # Check if switching to this would be rapid
            if self.safety_state.switch_timestamps:
                last_switch = self.safety_state.switch_timestamps[-1]
                if time.time() - last_switch < 1.0:
                    # Only allow critical candidates
                    if candidate.priority_score < 80:
                        continue
            
            safe_candidates.append(candidate)
        
        return safe_candidates
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def record_switch(self, scene: Any) -> None:
        """Record that a switch occurred"""
        
        self.safety_state.switch_timestamps.append(time.time())
        self.safety_state.current_scene_started_at = time.time()
        
        # Update team cooldowns
        if scene.team_id:
            self.safety_state.team_cooldowns[scene.team_id] = (
                time.time() + self.safety_config.team_cooldown_time
            )
            self.safety_state.recently_shown_teams.append(scene.team_id)
        
        # Track scene type
        self.safety_state.current_scene_type = scene.feed_type.value if hasattr(scene, 'feed_type') else ""

        # Phase 22 (W3, M6): final-zone scene tracking for the hard
        # lock. A scene counts as final-zone when it is flagged
        # final-circle or its feed type is the final-zone/map overview
        # in the late game (state.is_final_circle set by the director).
        feed_val = scene.feed_type.value if hasattr(scene, 'feed_type') else ""
        self.safety_state.current_is_final_zone = bool(
            getattr(scene, 'is_final_circle', False)
            or getattr(scene, 'is_final_zone', False)
            or (feed_val in ("final_zone", "map_overview") and self.safety_state.is_final_circle)
        )
        if self.safety_state.current_is_final_zone:
            self._apply_lock(
                self.safety_config.final_circle_stability,
                "Final zone coverage",
            )
        
        # Apply locks based on scene type
        if getattr(scene, 'is_clutch', False):
            self._apply_lock(
                self.safety_config.clutch_lock_time,
                "Clutch situation",
            )
            self.safety_state.is_clutch = True
        else:
            self.safety_state.is_clutch = False
        
        if getattr(scene, 'is_fight', False):
            self._apply_lock(
                self.safety_config.fight_lock_time,
                "Active fight",
            )
        
        if scene.feed_type.value == "replay" if hasattr(scene, 'feed_type') else False:
            self.safety_state.is_replay = True
        else:
            self.safety_state.is_replay = False
    
    def _apply_lock(self, duration: float, reason: str) -> None:
        """Apply a safety lock"""
        self.safety_state.locked_until = time.time() + duration
        self.safety_state.lock_reason = reason
        self._log.debug(f"Safety lock applied: {reason} ({duration}s)")
    
    def release_lock(self) -> None:
        """Release safety lock"""
        self.safety_state.locked_until = 0.0
        self.safety_state.lock_reason = ""
        self._log.debug("Safety lock released")
    
    def _record_violation(self, violation_type: SafetyViolation, details: str) -> None:
        """Record a safety violation"""
        self.safety_state.violations.append({
            "type": violation_type,
            "details": details,
            "timestamp": time.time(),
        })
        self.safety_state.violation_count += 1
        
        self._log.warning(f"Safety violation: {violation_type.value} - {details}")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OVERRIDE CONTROLS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def enable_manual_override(self) -> None:
        """Enable manual override (human control)"""
        self.manual_override = True
        self.release_lock()
        self._log.warning("Manual override ENABLED")
    
    def disable_manual_override(self) -> None:
        """Disable manual override"""
        self.manual_override = False
        self._log.warning("Manual override DISABLED")
    
    def enable_emergency_override(self) -> None:
        """Enable emergency override"""
        self.emergency_override = True
        self.release_lock()
        self._log.warning("⚠️ Emergency override ENABLED")
    
    def disable_emergency_override(self) -> None:
        """Disable emergency override"""
        self.emergency_override = False
        self._log.warning("Emergency override DISABLED")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_state(self) -> Dict[str, Any]:
        """Get current safety state"""
        recent_switches = [
            t for t in self.safety_state.switch_timestamps
            if time.time() - t < 60
        ]
        
        return {
            "locked": time.time() < self.safety_state.locked_until,
            "lock_reason": self.safety_state.lock_reason,
            "lock_remaining": max(0, self.safety_state.locked_until - time.time()),
            "is_clutch": self.safety_state.is_clutch,
            "is_replay": self.safety_state.is_replay,
            "switches_last_minute": len(recent_switches),
            "violation_count": self.safety_state.violation_count,
            "manual_override": self.manual_override,
            "emergency_override": self.emergency_override,
        }
    
    def get_violations(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent safety violations"""
        return list(self.safety_state.violations)[-limit:]
    
    def reset(self) -> None:
        """Reset safety state"""
        self.safety_state = SafetyState()
        self.manual_override = False
        self.emergency_override = False
        self._log.info("Safety controller reset")

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
