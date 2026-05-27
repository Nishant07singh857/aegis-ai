# backend/app/agents/commentary_agents/emotion_engine.py
"""
AEGIS AI - Emotion Engine
Modulates emotional tone of commentary based on match context.
Creates natural emotional arcs that follow the game's intensity.
"""

import time
from typing import Dict, List, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.emotion")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class EmotionalState(str, Enum):
    """Emotional states for commentary modulation"""
    CALM = "calm"               # Relaxed, casual
    INTERESTED = "interested"   # Engaged, watching
    EXCITED = "excited"         # Energy rising
    HYPED = "hyped"            # Maximum energy
    TENSE = "tense"            # Suspenseful
    DRAMATIC = "dramatic"      # Building tension
    CELEBRATORY = "celebratory" # Winning moment
    SHOCKED = "shocked"        # Surprised
    SOMBER = "somber"          # Respectful/sad
    ANALYTICAL = "analytical"  # Thoughtful

class EmotionIntensity(str, Enum):
    """Intensity levels for emotions"""
    VERY_LOW = "very_low"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"
    EXTREME = "extreme"

@dataclass
class EmotionProfile:
    """Current emotional profile for commentary"""
    state: EmotionalState = EmotionalState.CALM
    intensity: EmotionIntensity = EmotionIntensity.LOW
    energy_level: float = 30.0       # 0-100
    tension_level: float = 10.0      # 0-100
    excitement_level: float = 20.0   # 0-100
    
    # Modulation parameters
    speaking_rate: float = 1.0       # Words per second multiplier
    pitch_modifier: float = 1.0      # Voice pitch modifier
    volume_modifier: float = 0.8     # Volume modifier
    pause_frequency: float = 0.5     # How often to pause
    
    # History
    state_history: deque = field(default_factory=lambda: deque(maxlen=100))
    intensity_history: deque = field(default_factory=lambda: deque(maxlen=100))
    
    # Timing
    last_state_change: float = field(default_factory=time.time)
    state_duration: float = 0.0
    calm_down_rate: float = 2.0      # Points per second to calm down
    
    # Match context
    match_phase: str = "early"
    teams_alive: int = 16
    zone_phase: int = 0

@dataclass
class EmotionalArc:
    """Planned emotional journey through a match"""
    phases: List[Dict[str, Any]] = field(default_factory=list)
    current_phase: int = 0
    peak_intensity: float = 0.0
    
    def get_current_target(self) -> Dict[str, Any]:
        """Get target emotional state for current phase"""
        if self.current_phase < len(self.phases):
            return self.phases[self.current_phase]
        return {"state": EmotionalState.CALM, "intensity": EmotionIntensity.LOW}


class EmotionEngine:
    """
    Modulates commentary emotion based on match context.
    
    Creates natural emotional arcs:
    - Early game: Calm, analytical
    - First blood: Excitement spike
    - Mid game: Interested, varying
    - Fights: Excited, hyped
    - Clutch: Tense, dramatic
    - Final circle: Maximum tension
    - Match end: Celebratory or somber
    
    Ensures commentary doesn't feel:
    - Monotone (same energy throughout)
    - Random (unmotivated emotional shifts)
    - Exhausting (always at max energy)
    """
    
    def __init__(self):
        self.profile = EmotionProfile()
        self.arc = EmotionalArc()
        
        # Emotional decay rates
        self.decay_rates = {
            EmotionalState.CALM: 1.0,
            EmotionalState.INTERESTED: 1.5,
            EmotionalState.EXCITED: 3.0,
            EmotionalState.HYPED: 5.0,
            EmotionalState.TENSE: 2.0,
            EmotionalState.DRAMATIC: 2.5,
            EmotionalState.CELEBRATORY: 4.0,
            EmotionalState.SHOCKED: 4.0,
        }
        
        # State transition mapping
        self.transitions: Dict[EmotionalState, List[EmotionalState]] = {
            EmotionalState.CALM: [
                EmotionalState.INTERESTED, EmotionalState.EXCITED,
                EmotionalState.TENSE, EmotionalState.ANALYTICAL,
            ],
            EmotionalState.INTERESTED: [
                EmotionalState.EXCITED, EmotionalState.TENSE,
                EmotionalState.DRAMATIC, EmotionalState.CALM,
            ],
            EmotionalState.EXCITED: [
                EmotionalState.HYPED, EmotionalState.CELEBRATORY,
                EmotionalState.INTERESTED, EmotionalState.SHOCKED,
            ],
            EmotionalState.HYPED: [
                EmotionalState.CELEBRATORY, EmotionalState.EXCITED,
                EmotionalState.SHOCKED,
            ],
            EmotionalState.TENSE: [
                EmotionalState.DRAMATIC, EmotionalState.EXCITED,
                EmotionalState.INTERESTED,
            ],
            EmotionalState.DRAMATIC: [
                EmotionalState.TENSE, EmotionalState.EXCITED,
                EmotionalState.CELEBRATORY, EmotionalState.SOMBER,
            ],
            EmotionalState.CELEBRATORY: [
                EmotionalState.EXCITED, EmotionalState.HYPED,
                EmotionalState.CALM,
            ],
            EmotionalState.SHOCKED: [
                EmotionalState.EXCITED, EmotionalState.INTERESTED,
            ],
            EmotionalState.SOMBER: [
                EmotionalState.CALM, EmotionalState.ANALYTICAL,
            ],
            EmotionalState.ANALYTICAL: [
                EmotionalState.INTERESTED, EmotionalState.CALM,
            ],
        }
        
        self._log = logger.bind(component="emotion_engine")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EMOTION UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def update(
        self,
        dt: float,
        match_context: Dict[str, Any],
        active_events: List[Dict[str, Any]],
    ) -> EmotionalState:
        """
        Update emotional state based on match context.
        Called every tick to modulate emotions smoothly.
        
        Args:
            dt: Delta time since last update
            match_context: Current match state
            active_events: Active events (fights, kills, etc.)
            
        Returns:
            Current emotional state
        """
        
        # Update match context
        self.profile.match_phase = match_context.get("phase", "early")
        self.profile.teams_alive = match_context.get("teams_alive", 16)
        self.profile.zone_phase = match_context.get("zone_phase", 0)
        
        # Calculate target energy from events
        target_energy = self._calculate_target_energy(match_context, active_events)
        target_tension = self._calculate_target_tension(match_context, active_events)
        
        # Smoothly adjust energy and tension
        self.profile.energy_level = self._smooth_adjust(
            self.profile.energy_level,
            target_energy,
            dt,
            speed=5.0,
        )
        
        self.profile.tension_level = self._smooth_adjust(
            self.profile.tension_level,
            target_tension,
            dt,
            speed=3.0,
        )
        
        # Determine emotional state from energy and tension
        new_state = self._determine_state(
            self.profile.energy_level,
            self.profile.tension_level,
        )
        
        # Check if state transition is valid
        if new_state != self.profile.state:
            if new_state in self.transitions.get(self.profile.state, []):
                self._transition_to(new_state)
        
        # Natural decay (gradually calm down)
        self.profile.energy_level = max(0, self.profile.energy_level - 
                                       self.profile.calm_down_rate * dt * 0.3)
        
        # Update intensity
        self.profile.intensity = self._calculate_intensity()
        
        # Update modulation parameters
        self._update_modulation_params()
        
        # Update history
        self.profile.state_history.append({
            "state": self.profile.state.value,
            "energy": self.profile.energy_level,
            "tension": self.profile.tension_level,
            "timestamp": time.time(),
        })
        
        return self.profile.state
    
    def _calculate_target_energy(
        self,
        context: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate target energy level based on context"""
        energy = 20.0  # Base
        
        # Match phase energy
        phase_energy = {
            "early": 20,
            "mid": 30,
            "late": 45,
            "final_circle": 60,
            "match_end": 80,
        }
        energy += phase_energy.get(context.get("phase", "early"), 20)
        
        # Active fights boost energy
        active_fights = context.get("active_fights", 0)
        energy += active_fights * 10
        
        # Clutch situations
        if context.get("has_clutch", False):
            energy += 25
        
        # Recent kills
        recent_kills = context.get("recent_kills", 0)
        energy += recent_kills * 5
        
        # Zone phase
        zone_phase = context.get("zone_phase", 0)
        energy += zone_phase * 3
        
        # Teams alive (fewer = more intense)
        teams_alive = context.get("teams_alive", 16)
        if teams_alive <= 3:
            energy += 25
        elif teams_alive <= 5:
            energy += 15
        elif teams_alive <= 8:
            energy += 5
        
        # Event energy
        event_energy_map = {
            "clutch_win": 40,
            "team_wipe": 35,
            "multi_kill": 30,
            "clutch_start": 25,
            "fight_intense": 25,
            "kill": 10,
            "knock": 5,
            "match_end": 35,
            "match_start": 25,
        }
        
        for event in events:
            event_type = event.get("type", "")
            energy += event_energy_map.get(event_type, 5)
        
        return min(100, energy)
    
    def _calculate_target_tension(
        self,
        context: Dict[str, Any],
        events: List[Dict[str, Any]],
    ) -> float:
        """Calculate target tension level"""
        tension = 10.0  # Base
        
        # Zone phase tension
        zone_phase = context.get("zone_phase", 0)
        tension += zone_phase * 5
        
        # Clutch situations = maximum tension
        if context.get("has_clutch", False):
            tension += 35
        
        # Close fights
        if context.get("fight_close", False):
            tension += 20
        
        # Final circle
        if context.get("phase") == "final_circle":
            tension += 30
            if context.get("teams_alive", 16) <= 3:
                tension += 20
        
        # Event tension
        event_tension_map = {
            "clutch_start": 35,
            "clutch_progress": 30,
            "final_circle": 30,
            "fight_intense": 15,
            "zone_shrink": 20,
        }
        
        for event in events:
            event_type = event.get("type", "")
            tension += event_tension_map.get(event_type, 0)
        
        return min(100, tension)
    
    def _determine_state(self, energy: float, tension: float) -> EmotionalState:
        """Determine emotional state from energy and tension levels"""
        
        if energy >= 85:
            if tension >= 70:
                return EmotionalState.DRAMATIC
            return EmotionalState.HYPED
        
        if energy >= 65:
            if tension >= 60:
                return EmotionalState.TENSE
            return EmotionalState.EXCITED
        
        if energy >= 40:
            if tension >= 50:
                return EmotionalState.TENSE
            return EmotionalState.INTERESTED
        
        if tension >= 60:
            return EmotionalState.TENSE
        
        if energy >= 20:
            return EmotionalState.INTERESTED
        
        return EmotionalState.CALM
    
    def _transition_to(self, new_state: EmotionalState) -> None:
        """Transition to a new emotional state"""
        old_state = self.profile.state
        self.profile.state = new_state
        self.profile.last_state_change = time.time()
        
        # Adjust energy and tension for new state
        state_params = {
            EmotionalState.CALM: (20, 10),
            EmotionalState.INTERESTED: (40, 20),
            EmotionalState.EXCITED: (65, 30),
            EmotionalState.HYPED: (85, 40),
            EmotionalState.TENSE: (40, 75),
            EmotionalState.DRAMATIC: (70, 85),
            EmotionalState.CELEBRATORY: (90, 30),
            EmotionalState.SHOCKED: (80, 50),
            EmotionalState.SOMBER: (10, 20),
            EmotionalState.ANALYTICAL: (25, 15),
        }
        
        target_energy, target_tension = state_params.get(new_state, (30, 20))
        self.profile.energy_level = (self.profile.energy_level + target_energy) / 2
        self.profile.tension_level = (self.profile.tension_level + target_tension) / 2
        
        self._log.debug(
            f"Emotion transition: {old_state.value} -> {new_state.value}",
            energy=round(self.profile.energy_level, 1),
            tension=round(self.profile.tension_level, 1),
        )
    
    def _calculate_intensity(self) -> EmotionIntensity:
        """Calculate intensity from energy level"""
        if self.profile.energy_level >= 85:
            return EmotionIntensity.EXTREME
        elif self.profile.energy_level >= 70:
            return EmotionIntensity.VERY_HIGH
        elif self.profile.energy_level >= 50:
            return EmotionIntensity.HIGH
        elif self.profile.energy_level >= 30:
            return EmotionIntensity.MEDIUM
        elif self.profile.energy_level >= 15:
            return EmotionIntensity.LOW
        return EmotionIntensity.VERY_LOW
    
    def _update_modulation_params(self) -> None:
        """Update voice modulation parameters based on emotion"""
        
        energy = self.profile.energy_level / 100
        
        # Speaking rate: faster when excited
        self.profile.speaking_rate = 0.8 + energy * 0.5
        
        # Pitch: higher when excited
        self.profile.pitch_modifier = 0.9 + energy * 0.3
        
        # Volume: louder when excited
        self.profile.volume_modifier = 0.7 + energy * 0.3
        
        # Pause frequency: less pauses when excited
        self.profile.pause_frequency = max(0.2, 0.6 - energy * 0.4)
    
    def _smooth_adjust(
        self,
        current: float,
        target: float,
        dt: float,
        speed: float = 3.0,
    ) -> float:
        """Smoothly adjust value toward target"""
        diff = target - current
        change = diff * min(1.0, speed * dt)
        return current + change
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT TRIGGERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def on_kill(self, is_multi_kill: bool = False) -> None:
        """Handle kill event"""
        boost = 15 if is_multi_kill else 5
        self.profile.energy_level = min(100, self.profile.energy_level + boost)
    
    def on_clutch_start(self) -> None:
        """Handle clutch start"""
        self.profile.tension_level = min(100, self.profile.tension_level + 30)
        self.profile.energy_level = min(100, self.profile.energy_level + 20)
    
    def on_clutch_win(self) -> None:
        """Handle clutch win - maximum excitement"""
        self.profile.energy_level = min(100, self.profile.energy_level + 40)
        self.profile.tension_level = max(0, self.profile.tension_level - 20)
        self._transition_to(EmotionalState.CELEBRATORY)
    
    def on_team_wipe(self) -> None:
        """Handle team wipe"""
        self.profile.energy_level = min(100, self.profile.energy_level + 25)
    
    def on_final_circle(self) -> None:
        """Handle final circle"""
        self.profile.tension_level = min(100, self.profile.tension_level + 25)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_current_profile(self) -> Dict[str, Any]:
        """Get current emotional profile"""
        return {
            "state": self.profile.state.value,
            "intensity": self.profile.intensity.value,
            "energy": round(self.profile.energy_level, 1),
            "tension": round(self.profile.tension_level, 1),
            "speaking_rate": round(self.profile.speaking_rate, 2),
            "pitch_modifier": round(self.profile.pitch_modifier, 2),
            "volume_modifier": round(self.profile.volume_modifier, 2),
            "match_phase": self.profile.match_phase,
        }
    
    def get_emotional_arc_progress(self) -> float:
        """Get progress through emotional arc (0-1)"""
        if not self.arc.phases:
            return 0.0
        return min(1.0, self.arc.current_phase / len(self.arc.phases))
    
    def get_recommended_tone(self) -> str:
        """Get recommended commentary tone based on emotion"""
        tone_map = {
            EmotionalState.CALM: "casual",
            EmotionalState.INTERESTED: "analytical",
            EmotionalState.EXCITED: "excited",
            EmotionalState.HYPED: "excited",
            EmotionalState.TENSE: "tense",
            EmotionalState.DRAMATIC: "dramatic",
            EmotionalState.CELEBRATORY: "celebratory",
            EmotionalState.SHOCKED: "shocked",
            EmotionalState.SOMBER: "respectful",
            EmotionalState.ANALYTICAL: "analytical",
        }
        return tone_map.get(self.profile.state, "casual")
    
    def reset(self) -> None:
        """Reset emotion engine"""
        self.profile = EmotionProfile()
        self.arc = EmotionalArc()
        self._log.info("Emotion engine reset")