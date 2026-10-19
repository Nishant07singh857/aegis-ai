# backend/app/broadcast/transition_controller.py
"""
AEGIS AI - Transition Controller
Manages scene transitions with professional effects.
Supports cuts, fades, wipes, stinger transitions, and more.
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.transitions")


class TransitionType(str, Enum):
    """Types of scene transitions"""
    CUT = "cut_transition"
    FADE = "fade_transition"
    SWIPE = "swipe_transition"
    SLIDE = "slide_transition"
    STINGER = "stinger_transition"
    LUMAS = "luma_wipe_transition"


class EasingType(str, Enum):
    """Transition easing functions"""
    LINEAR = "linear"
    EASE_IN = "ease_in"
    EASE_OUT = "ease_out"
    EASE_IN_OUT = "ease_in_out"
    BOUNCE = "bounce"
    ELASTIC = "elastic"


@dataclass
class Transition:
    """A scene transition"""
    name: str
    transition_type: TransitionType = TransitionType.CUT
    duration_ms: int = 300
    easing: EasingType = EasingType.EASE_IN_OUT
    
    # Fade specific
    fade_color: int = 0x000000  # Black
    
    # Swipe/Slide specific
    direction: str = "left"  # left, right, up, down
    
    # Stinger specific
    stinger_file: str = ""
    stinger_trigger_point: float = 0.5  # When scene switches during stinger


class TransitionController:
    """
    Controls scene transitions for broadcast.
    
    Features:
    - Multiple transition types
    - Customizable duration and easing
    - Transition presets for different situations
    - Auto-transition based on conditions
    - Transition queuing
    """
    
    def __init__(self, obs_client: Any):
        self.obs = obs_client
        self.transitions: Dict[str, Transition] = {}
        self.active_transition: str = "Cut"
        self.transition_queue: List[Transition] = []
        
        # Setup default transitions
        self._setup_defaults()
        
        self._log = logger.bind(component="transition_controller")
    
    def _setup_defaults(self) -> None:
        """Setup default transitions"""
        
        defaults = [
            Transition("Cut", TransitionType.CUT, 0),
            Transition("Quick Fade", TransitionType.FADE, 200),
            Transition("Slow Fade", TransitionType.FADE, 1000),
            Transition("Swipe Left", TransitionType.SWIPE, 400, direction="left"),
            Transition("Swipe Right", TransitionType.SWIPE, 400, direction="right"),
            Transition("Slide Up", TransitionType.SLIDE, 500, direction="up"),
            Transition("Dramatic Fade", TransitionType.FADE, 2000, easing=EasingType.EASE_OUT),
        ]
        
        for transition in defaults:
            self.transitions[transition.name] = transition
    
    async def set_transition(self, name: str, duration_ms: Optional[int] = None) -> bool:
        """Set the active transition type"""
        
        transition = self.transitions.get(name)
        if not transition:
            self._log.error(f"Transition not found: {name}")
            return False
        
        settings = {}
        
        if transition.transition_type == TransitionType.FADE:
            settings["fadeColor"] = transition.fade_color
        
        if transition.transition_type in [TransitionType.SWIPE, TransitionType.SLIDE]:
            settings["direction"] = transition.direction
        
        response = await self.obs.send_command("SetCurrentSceneTransition", {
            "transitionName": transition.transition_type.value,
        })
        
        if response.success:
            duration = duration_ms or transition.duration_ms
            await self.obs.send_command("SetCurrentSceneTransitionDuration", {
                "transitionDuration": duration,
            })
            
            self.active_transition = name
            
            self._log.info(f"Transition set: {name} ({duration}ms)")
            return True
        
        return False
    
    async def auto_transition(self) -> bool:
        """Trigger the current transition"""
        response = await self.obs.send_command("TriggerStudioModeTransition")
        return response.success
    
    async def set_duration(self, duration_ms: int) -> bool:
        """Set transition duration"""
        response = await self.obs.send_command("SetCurrentSceneTransitionDuration", {
            "transitionDuration": duration_ms,
        })
        return response.success
    
    def get_transition_for_moment(self, moment_type: str) -> str:
        """Get appropriate transition for a broadcast moment.

        Visual Broadcast Bible §3.2 (frame analysis, not guesses): every
        camera change is a hard cut (1 frame) — zero fades, zero wipes
        were observed. All moments therefore cut.
        """

        moment_transitions = {
            "fight_start": "Cut",
            "clutch": "Cut",
            "replay": "Cut",
            "match_end": "Cut",
            "analysis": "Cut",
            "default": "Cut",
        }

        return moment_transitions.get(moment_type, "Cut")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get transition controller statistics"""
        return {
            "active_transition": self.active_transition,
            "available_transitions": list(self.transitions.keys()),
            "queue_size": len(self.transition_queue),
        }