# backend/app/broadcast/transition_controller.py
"""
AEGIS AI - Transition Controller
Manages scene transitions with professional effects.

Visual Broadcast Bible rule (inspection H15): camera changes are
hard cuts. FADE and SWIPE transitions are Bible-banned — they are no
longer registered as types or defaults here, and ``set_transition``
rejects them with a clear error so a mistaken wiring cannot put a
banned transition on air. (This module is dormant today — zero
production callers — which is exactly why the banned types had to be
removed before any wiring happens.)
"""

from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.transitions")


class TransitionType(str, Enum):
    """Types of scene transitions (Bible-legal only).

    FADE and SWIPE existed here before (inspection H15) and were
    removed: they are banned by the Visual Broadcast Bible. Do not
    re-add them; ``set_transition`` also rejects their names/values
    explicitly in case a banned transition is constructed elsewhere.
    """
    CUT = "cut_transition"
    SLIDE = "slide_transition"
    STINGER = "stinger_transition"
    LUMAS = "luma_wipe_transition"


#: Names and type-values banned by the Visual Broadcast Bible.
BANNED_TRANSITION_NAMES = frozenset({
    "quick fade", "slow fade", "dramatic fade", "fade",
    "swipe left", "swipe right", "swipe",
})
BANNED_TRANSITION_VALUES = frozenset({
    "fade_transition", "swipe_transition", "fade", "swipe",
})


class BannedTransitionError(ValueError):
    """Raised when a Bible-banned transition (fade/swipe) is requested."""


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
        """Setup default transitions (Bible-legal only).

        The previous defaults registered three fades and two swipes
        with activatable durations (inspection H15); they are gone.
        """

        defaults = [
            Transition("Cut", TransitionType.CUT, 0),
            Transition("Slide Up", TransitionType.SLIDE, 500, direction="up"),
        ]

        for transition in defaults:
            self.transitions[transition.name] = transition

    async def set_transition(self, name: str, duration_ms: Optional[int] = None) -> bool:
        """Set the active transition type.

        Raises:
            BannedTransitionError: if ``name`` (or the registered
                transition's type) is a Bible-banned fade/swipe. A banned
                transition is a clear error, never a silent activation.
        """

        if name.strip().lower() in BANNED_TRANSITION_NAMES:
            raise BannedTransitionError(
                f"Transition {name!r} is banned by the Visual Broadcast "
                "Bible (no fades, no swipes — hard cuts only)."
            )

        transition = self.transitions.get(name)
        if not transition:
            self._log.error(f"Transition not found: {name}")
            return False

        type_value = str(getattr(transition.transition_type, "value", transition.transition_type)).lower()
        if type_value in BANNED_TRANSITION_VALUES:
            raise BannedTransitionError(
                f"Transition {name!r} uses banned type {type_value!r} "
                "(Visual Broadcast Bible: no fades, no swipes)."
            )

        settings = {}

        if transition.transition_type == TransitionType.SLIDE:
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