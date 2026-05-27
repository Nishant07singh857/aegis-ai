# backend/app/agents/observer_agents/cinematic_controller.py
"""
AEGIS AI - Cinematic Controller
Controls camera transitions, movements, and cinematic effects
to create professional broadcast-quality visuals.
"""

import time
import math
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.cinematic")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class TransitionType(str, Enum):
    """Camera transition types"""
    CUT = "cut"               # Instant cut
    FADE = "fade"            # Fade to black
    CROSSFADE = "crossfade"  # Smooth blend
    SWIPE = "swipe"          # Directional swipe
    ZOOM_IN = "zoom_in"      # Zoom into target
    ZOOM_OUT = "zoom_out"    # Zoom out from target
    PAN = "pan"              # Pan across
    WHIP_PAN = "whip_pan"    # Fast pan
    DUTCH_ANGLE = "dutch_angle"  # Tilted angle
    SMOOTH = "smooth"        # Smooth interpolation

class CameraMovement(str, Enum):
    """Camera movement patterns"""
    STATIC = "static"
    SLOW_PAN = "slow_pan"
    FAST_PAN = "fast_pan"
    ORBIT = "orbit"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    TRACKING = "tracking"
    DOLLY = "dolly"
    CRANE = "crane"
    HANDHELD = "handheld"

class CinematicEffect(str, Enum):
    """Cinematic visual effects"""
    NONE = "none"
    SLOW_MOTION = "slow_motion"
    SPEED_RAMP = "speed_ramp"
    DEPTH_OF_FIELD = "depth_of_field"
    VIGNETTE = "vignette"
    COLOR_GRADE = "color_grade"
    LENS_FLARE = "lens_flare"
    MOTION_BLUR = "motion_blur"
    SHAKE = "shake"

@dataclass
class CameraState:
    """Current camera state"""
    position_x: float = 0.0
    position_y: float = 0.0
    position_z: float = 100.0
    rotation_pitch: float = -30.0  # Looking down angle
    rotation_yaw: float = 0.0
    rotation_roll: float = 0.0
    fov: float = 90.0  # Field of view
    zoom_level: float = 1.0
    
    # Movement
    movement_type: CameraMovement = CameraMovement.STATIC
    target_x: float = 0.0
    target_y: float = 0.0
    target_z: float = 0.0
    movement_speed: float = 1.0
    
    # Effects
    active_effects: List[CinematicEffect] = field(default_factory=list)
    
    # Timing
    transition_start: float = 0.0
    transition_duration: float = 1.0
    transition_progress: float = 0.0

@dataclass
class CinematicSequence:
    """A pre-planned cinematic sequence"""
    sequence_id: str
    name: str
    steps: List[Dict[str, Any]]
    current_step: int = 0
    started_at: float = field(default_factory=time.time)
    is_active: bool = False
    loop: bool = False


class CinematicController:
    """
    Controls camera movements and transitions for broadcast.
    
    Creates professional broadcast-quality visuals:
    - Smooth camera transitions
    - Cinematic movement patterns
    - Dynamic camera following
    - Special effects (slow-mo, depth of field)
    - Pre-planned cinematic sequences
    - Match-specific camera presets
    """
    
    def __init__(self):
        # Current camera state
        self.camera = CameraState()
        
        # Previous camera state (for transitions)
        self.previous_camera: Optional[CameraState] = None
        
        # Active sequences
        self.active_sequences: Dict[str, CinematicSequence] = {}
        self.sequence_history: deque = deque(maxlen=20)
        
        # Transition rules
        self.transition_rules = {
            TransitionType.CUT: {"duration": 0.0, "smooth": False},
            TransitionType.FADE: {"duration": 0.5, "smooth": True},
            TransitionType.CROSSFADE: {"duration": 0.75, "smooth": True},
            TransitionType.SWIPE: {"duration": 0.5, "smooth": False},
            TransitionType.ZOOM_IN: {"duration": 1.0, "smooth": True},
            TransitionType.ZOOM_OUT: {"duration": 1.0, "smooth": True},
            TransitionType.PAN: {"duration": 0.75, "smooth": True},
            TransitionType.WHIP_PAN: {"duration": 0.3, "smooth": False},
            TransitionType.SMOOTH: {"duration": 1.5, "smooth": True},
        }
        
        # Cinematic presets for different situations
        self.presets = {
            "fight_start": {
                "transition": TransitionType.WHIP_PAN,
                "movement": CameraMovement.FAST_PAN,
                "fov": 80,
            },
            "clutch": {
                "transition": TransitionType.ZOOM_IN,
                "movement": CameraMovement.TRACKING,
                "fov": 70,
                "effects": [CinematicEffect.DEPTH_OF_FIELD],
            },
            "final_circle": {
                "transition": TransitionType.SMOOTH,
                "movement": CameraMovement.SLOW_PAN,
                "fov": 100,
                "effects": [CinematicEffect.VIGNETTE],
            },
            "replay": {
                "transition": TransitionType.FADE,
                "movement": CameraMovement.STATIC,
                "effects": [CinematicEffect.SLOW_MOTION],
            },
            "cinematic": {
                "transition": TransitionType.CROSSFADE,
                "movement": CameraMovement.ORBIT,
                "fov": 85,
                "effects": [CinematicEffect.COLOR_GRADE],
            },
            "player_follow": {
                "transition": TransitionType.SMOOTH,
                "movement": CameraMovement.TRACKING,
                "fov": 75,
            },
        }
        
        # Movement speed presets
        self.movement_speeds = {
            CameraMovement.STATIC: 0.0,
            CameraMovement.SLOW_PAN: 0.3,
            CameraMovement.FAST_PAN: 2.0,
            CameraMovement.ORBIT: 0.5,
            CameraMovement.TRACKING: 0.8,
            CameraMovement.DOLLY: 0.4,
            CameraMovement.CRANE: 0.6,
            CameraMovement.HANDHELD: 1.5,
        }
        
        self._log = logger.bind(component="cinematic")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRANSITION MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_transition(
        self,
        current_scene: Any,
        candidate: Any,
    ) -> TransitionType:
        """Determine the best transition type for a scene change"""
        
        # If no current scene, just cut
        if not current_scene:
            return TransitionType.CUT
        
        # If switching to clutch, use dramatic transition
        if getattr(candidate, 'is_clutch', False):
            return TransitionType.ZOOM_IN
        
        # If switching fight, use fast pan
        if getattr(candidate, 'is_fight', False) and current_scene.fight_id != candidate.fight_id:
            return TransitionType.WHIP_PAN
        
        # If same team, smooth transition
        if (current_scene.team_id and candidate.team_id and 
            current_scene.team_id == candidate.team_id):
            return TransitionType.SMOOTH
        
        # If final circle, use cinematic
        if getattr(candidate, 'is_final_circle', False):
            return TransitionType.CROSSFADE
        
        # Default: cut
        return TransitionType.CUT
    
    async def apply_transition(self, transition: TransitionType) -> None:
        """Apply a camera transition"""
        rules = self.transition_rules.get(transition, {"duration": 0.5})
        
        # Save previous camera state
        self.previous_camera = CameraState(
            position_x=self.camera.position_x,
            position_y=self.camera.position_y,
            position_z=self.camera.position_z,
            rotation_pitch=self.camera.rotation_pitch,
            rotation_yaw=self.camera.rotation_yaw,
            fov=self.camera.fov,
        )
        
        # Set transition
        self.camera.transition_start = time.time()
        self.camera.transition_duration = rules["duration"]
        self.camera.transition_progress = 0.0
        
        self._log.debug(f"Transition applied: {transition.value}")
    
    def get_current_camera_state(self) -> Dict[str, Any]:
        """Get current camera state for OBS"""
        
        # Calculate transition progress
        if self.camera.transition_duration > 0:
            elapsed = time.time() - self.camera.transition_start
            self.camera.transition_progress = min(
                1.0,
                elapsed / self.camera.transition_duration,
            )
        
        # Interpolate if in transition
        if (self.previous_camera and 
            self.camera.transition_progress < 1.0):
            t = self.camera.transition_progress
            # Smooth easing
            t = t * t * (3 - 2 * t)  # Smoothstep
            
            return {
                "position": {
                    "x": self._lerp(self.previous_camera.position_x, self.camera.position_x, t),
                    "y": self._lerp(self.previous_camera.position_y, self.camera.position_y, t),
                    "z": self._lerp(self.previous_camera.position_z, self.camera.position_z, t),
                },
                "rotation": {
                    "pitch": self._lerp(self.previous_camera.rotation_pitch, self.camera.rotation_pitch, t),
                    "yaw": self._lerp_angle(self.previous_camera.rotation_yaw, self.camera.rotation_yaw, t),
                    "roll": self.camera.rotation_roll,
                },
                "fov": self._lerp(self.previous_camera.fov, self.camera.fov, t),
                "zoom": self.camera.zoom_level,
                "effects": [e.value for e in self.camera.active_effects],
                "transition_progress": self.camera.transition_progress,
            }
        
        return {
            "position": {
                "x": self.camera.position_x,
                "y": self.camera.position_y,
                "z": self.camera.position_z,
            },
            "rotation": {
                "pitch": self.camera.rotation_pitch,
                "yaw": self.camera.rotation_yaw,
                "roll": self.camera.rotation_roll,
            },
            "fov": self.camera.fov,
            "zoom": self.camera.zoom_level,
            "effects": [e.value for e in self.camera.active_effects],
            "transition_progress": 1.0,
        }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CAMERA MOVEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_camera_target(
        self,
        x: float,
        y: float,
        z: float = 100.0,
        movement: CameraMovement = CameraMovement.TRACKING,
        look_at_x: Optional[float] = None,
        look_at_y: Optional[float] = None,
    ) -> None:
        """Set camera target position"""
        
        self.camera.target_x = x
        self.camera.target_y = y
        self.camera.target_z = z
        self.camera.movement_type = movement
        self.camera.movement_speed = self.movement_speeds.get(movement, 1.0)
        
        # Calculate look-at rotation
        if look_at_x is not None and look_at_y is not None:
            dx = look_at_x - x
            dy = look_at_y - y
            self.camera.rotation_yaw = math.degrees(math.atan2(dy, dx))
    
    def apply_preset(self, preset_name: str) -> None:
        """Apply a cinematic preset"""
        preset = self.presets.get(preset_name)
        if not preset:
            self._log.warning(f"Preset not found: {preset_name}")
            return
        
        # Apply transition
        if "transition" in preset:
            self.camera.transition_duration = self.transition_rules.get(
                preset["transition"], {"duration": 0.5}
            )["duration"]
        
        # Apply movement
        if "movement" in preset:
            self.camera.movement_type = preset["movement"]
            self.camera.movement_speed = self.movement_speeds.get(
                preset["movement"], 1.0
            )
        
        # Apply FOV
        if "fov" in preset:
            self.camera.fov = preset["fov"]
        
        # Apply effects
        if "effects" in preset:
            self.camera.active_effects = preset["effects"]
        
        self._log.debug(f"Preset applied: {preset_name}")
    
    def start_cinematic_sequence(self, sequence_id: str, name: str, steps: List[Dict[str, Any]], loop: bool = False) -> None:
        """Start a pre-planned cinematic sequence"""
        sequence = CinematicSequence(
            sequence_id=sequence_id,
            name=name,
            steps=steps,
            loop=loop,
            is_active=True,
        )
        
        self.active_sequences[sequence_id] = sequence
        self._log.info(f"Cinematic sequence started: {name}")
    
    def update_sequence(self) -> Optional[Dict[str, Any]]:
        """Update active cinematic sequence, return next step if available"""
        
        for seq_id, sequence in list(self.active_sequences.items()):
            if not sequence.is_active:
                self.sequence_history.append(sequence)
                del self.active_sequences[seq_id]
                continue
            
            if sequence.current_step < len(sequence.steps):
                step = sequence.steps[sequence.current_step]
                sequence.current_step += 1
                return step
            elif sequence.loop:
                sequence.current_step = 0
                return sequence.steps[0]
            else:
                sequence.is_active = False
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EFFECTS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def enable_effect(self, effect: CinematicEffect) -> None:
        """Enable a cinematic effect"""
        if effect not in self.camera.active_effects:
            self.camera.active_effects.append(effect)
            self._log.debug(f"Effect enabled: {effect.value}")
    
    def disable_effect(self, effect: CinematicEffect) -> None:
        """Disable a cinematic effect"""
        if effect in self.camera.active_effects:
            self.camera.active_effects.remove(effect)
            self._log.debug(f"Effect disabled: {effect.value}")
    
    def clear_effects(self) -> None:
        """Clear all active effects"""
        self.camera.active_effects.clear()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _lerp(self, a: float, b: float, t: float) -> float:
        """Linear interpolation"""
        return a + (b - a) * t
    
    def _lerp_angle(self, a: float, b: float, t: float) -> float:
        """Angle interpolation (handles wrap-around)"""
        diff = (b - a) % 360
        if diff > 180:
            diff -= 360
        return a + diff * t
    
    def get_state(self) -> Dict[str, Any]:
        """Get complete camera state"""
        return {
            "camera": self.get_current_camera_state(),
            "movement": self.camera.movement_type.value,
            "active_sequences": len(self.active_sequences),
            "active_effects": [e.value for e in self.camera.active_effects],
        }
    
    def reset(self) -> None:
        """Reset camera to default state"""
        self.camera = CameraState()
        self.previous_camera = None
        self.active_sequences.clear()
        self._log.info("Cinematic controller reset")