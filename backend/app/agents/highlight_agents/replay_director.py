# backend/app/agents/highlight_agents/replay_director.py
"""
AEGIS AI - Replay Director
Manages replay sequences, selects best angles,
controls slow-motion timing, and creates professional replays.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.replay_director")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ReplayAngle(str, Enum):
    """Available replay angles"""
    PLAYER_POV = "player_pov"
    ENEMY_POV = "enemy_pov"
    OVERHEAD = "overhead"
    FREECAM = "freecam"
    FOLLOW_ACTION = "follow_action"
    BULLET_TRAJECTORY = "bullet_trajectory"
    EXPLOSION_CLOSEUP = "explosion_closeup"
    SCOPE_VIEW = "scope_view"
    SPLIT_SCREEN = "split_screen"
    DRONE_SHOT = "drone_shot"

class SpeedPreset(str, Enum):
    """Replay speed presets"""
    SUPER_SLOW = "super_slow"     # 0.25x
    SLOW = "slow"                # 0.5x
    NORMAL = "normal"            # 1.0x
    FAST = "fast"                # 1.5x
    SPEED_RAMP = "speed_ramp"    # Variable speed

@dataclass
class ReplayShot:
    """A single shot in a replay sequence"""
    shot_id: str
    angle: ReplayAngle
    start_offset_ms: float = 0.0  # Offset from replay start
    duration_ms: float = 5000.0
    speed: SpeedPreset = SpeedPreset.NORMAL
    target_player: str = ""
    target_team: str = ""
    description: str = ""
    transition: str = "cut"  # cut, fade, dissolve

@dataclass
class ReplaySequence:
    """Complete replay sequence"""
    sequence_id: str
    source_clip_id: str
    title: str = ""
    
    # Timing
    total_duration_ms: float = 0.0
    created_at: float = field(default_factory=time.time)
    
    # Shots
    shots: List[ReplayShot] = field(default_factory=list)
    current_shot_index: int = 0
    
    # Production
    intro_duration_ms: float = 2000.0
    outro_duration_ms: float = 2000.0
    has_intro: bool = True
    has_outro: bool = True
    watermark_text: str = ""
    
    # Status
    is_playing: bool = False
    is_ready: bool = True
    play_count: int = 0


class ReplayDirector:
    """
    Directs professional replay sequences.
    
    Creates multi-angle replays with:
    - Optimal angle selection
    - Slow-motion timing
    - Smooth transitions
    - Speed ramping
    - Split-screen when appropriate
    - Professional intro/outro
    """
    
    def __init__(self):
        # Replay sequences
        self.sequences: Dict[str, ReplaySequence] = {}
        self.sequence_queue: deque = deque(maxlen=20)
        self.active_sequence: Optional[ReplaySequence] = None
        
        # Angle preferences per clip type
        self.angle_preferences = {
            "clutch_win": [
                ReplayAngle.PLAYER_POV,
                ReplayAngle.ENEMY_POV,
                ReplayAngle.OVERHEAD,
                ReplayAngle.SPLIT_SCREEN,
            ],
            "squad_wipe": [
                ReplayAngle.PLAYER_POV,
                ReplayAngle.OVERHEAD,
                ReplayAngle.FOLLOW_ACTION,
            ],
            "multi_kill": [
                ReplayAngle.PLAYER_POV,
                ReplayAngle.FREECAM,
                ReplayAngle.SPLIT_SCREEN,
            ],
            "grenade_kill": [
                ReplayAngle.PLAYER_POV,
                ReplayAngle.EXPLOSION_CLOSEUP,
                ReplayAngle.OVERHEAD,
                ReplayAngle.BULLET_TRAJECTORY,
            ],
            "long_range": [
                ReplayAngle.SCOPE_VIEW,
                ReplayAngle.BULLET_TRAJECTORY,
                ReplayAngle.PLAYER_POV,
            ],
            "final_circle": [
                ReplayAngle.OVERHEAD,
                ReplayAngle.DRONE_SHOT,
                ReplayAngle.PLAYER_POV,
            ],
        }
        
        # Speed presets duration multipliers
        self.speed_multipliers = {
            SpeedPreset.SUPER_SLOW: 4.0,
            SpeedPreset.SLOW: 2.0,
            SpeedPreset.NORMAL: 1.0,
            SpeedPreset.FAST: 0.67,
            SpeedPreset.SPEED_RAMP: 2.5,
        }
        
        self._log = logger.bind(component="replay_director")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REPLAY CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def create_replay(
        self,
        clip_data: Dict[str, Any],
        available_angles: List[ReplayAngle],
    ) -> ReplaySequence:
        """Create a professional replay sequence from a clip"""
        
        sequence = ReplaySequence(
            sequence_id=f"replay_{int(time.time())}_{clip_data.get('clip_id', 'unknown')[:8]}",
            source_clip_id=clip_data.get("clip_id", ""),
            title=clip_data.get("title", "Replay"),
            watermark_text=clip_data.get("primary_player", clip_data.get("primary_team", "")),
        )
        
        # Select best angles
        category = clip_data.get("category", "multi_kill")
        preferred_angles = self.angle_preferences.get(category, [
            ReplayAngle.PLAYER_POV,
            ReplayAngle.OVERHEAD,
        ])
        
        # Filter to available angles
        usable_angles = [a for a in preferred_angles if a in available_angles]
        if not usable_angles:
            usable_angles = [ReplayAngle.PLAYER_POV]
        
        # Create shots
        total_duration_ms = 0.0
        clip_duration_ms = clip_data.get("duration_seconds", 10) * 1000
        
        # Intro shot
        if sequence.has_intro:
            intro_shot = ReplayShot(
                shot_id=f"{sequence.sequence_id}_intro",
                angle=ReplayAngle.DRONE_SHOT if ReplayAngle.DRONE_SHOT in available_angles else usable_angles[0],
                start_offset_ms=-sequence.intro_duration_ms,
                duration_ms=sequence.intro_duration_ms,
                speed=SpeedPreset.NORMAL,
                description="Intro",
                transition="fade",
            )
            sequence.shots.append(intro_shot)
            total_duration_ms += sequence.intro_duration_ms
        
        # Main action shots
        shots_per_angle = clip_duration_ms / len(usable_angles)
        
        for i, angle in enumerate(usable_angles[:3]):  # Max 3 angles
            # Determine speed
            if i == 0:  # First angle: slow-mo for impact
                speed = SpeedPreset.SLOW
            elif i == len(usable_angles) - 1:  # Last angle: speed ramp
                speed = SpeedPreset.SPEED_RAMP
            else:
                speed = SpeedPreset.NORMAL
            
            shot = ReplayShot(
                shot_id=f"{sequence.sequence_id}_action_{i}",
                angle=angle,
                start_offset_ms=i * shots_per_angle,
                duration_ms=shots_per_angle,
                speed=speed,
                target_player=clip_data.get("primary_player", ""),
                target_team=clip_data.get("primary_team", ""),
                description=f"{angle.value} replay",
                transition="dissolve" if i > 0 else "cut",
            )
            sequence.shots.append(shot)
            total_duration_ms += shots_per_angle * self.speed_multipliers[speed]
        
        # Outro shot
        if sequence.has_outro:
            outro_shot = ReplayShot(
                shot_id=f"{sequence.sequence_id}_outro",
                angle=ReplayAngle.OVERHEAD if ReplayAngle.OVERHEAD in available_angles else usable_angles[-1],
                start_offset_ms=clip_duration_ms,
                duration_ms=sequence.outro_duration_ms,
                speed=SpeedPreset.NORMAL,
                description="Outro",
                transition="fade",
            )
            sequence.shots.append(outro_shot)
            total_duration_ms += sequence.outro_duration_ms
        
        sequence.total_duration_ms = total_duration_ms
        
        # Store sequence
        self.sequences[sequence.sequence_id] = sequence
        self.sequence_queue.append(sequence)
        
        self._log.info(
            f"Replay created: {sequence.title}",
            shots=len(sequence.shots),
            duration_ms=total_duration_ms,
        )
        
        return sequence
    
    def play_replay(self, sequence_id: str) -> bool:
        """Start playing a replay sequence"""
        
        sequence = self.sequences.get(sequence_id)
        if not sequence:
            return False
        
        if self.active_sequence and self.active_sequence.is_playing:
            self.stop_replay(self.active_sequence.sequence_id)
        
        sequence.is_playing = True
        sequence.current_shot_index = 0
        sequence.play_count += 1
        self.active_sequence = sequence
        
        self._log.info(f"Replay playing: {sequence.title}")
        
        return True
    
    def stop_replay(self, sequence_id: str) -> bool:
        """Stop a replay sequence"""
        
        sequence = self.sequences.get(sequence_id)
        if not sequence:
            return False
        
        sequence.is_playing = False
        
        if self.active_sequence == sequence:
            self.active_sequence = None
        
        return True
    
    def get_current_shot(self) -> Optional[Dict[str, Any]]:
        """Get current shot in active replay"""
        
        if not self.active_sequence or not self.active_sequence.is_playing:
            return None
        
        sequence = self.active_sequence
        
        if sequence.current_shot_index >= len(sequence.shots):
            return None
        
        shot = sequence.shots[sequence.current_shot_index]
        
        return {
            "angle": shot.angle.value,
            "speed": shot.speed.value,
            "target_player": shot.target_player,
            "target_team": shot.target_team,
            "duration_ms": shot.duration_ms,
            "transition": shot.transition,
            "description": shot.description,
        }
    
    def advance_shot(self) -> bool:
        """Advance to next shot in active replay"""
        
        if not self.active_sequence:
            return False
        
        sequence = self.active_sequence
        sequence.current_shot_index += 1
        
        if sequence.current_shot_index >= len(sequence.shots):
            sequence.is_playing = False
            self.active_sequence = None
            return False
        
        return True
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_replay(self, sequence_id: str) -> Optional[Dict[str, Any]]:
        """Get replay sequence details"""
        
        sequence = self.sequences.get(sequence_id)
        if not sequence:
            return None
        
        return {
            "sequence_id": sequence.sequence_id,
            "title": sequence.title,
            "total_duration_ms": sequence.total_duration_ms,
            "shots": [
                {
                    "angle": s.angle.value,
                    "speed": s.speed.value,
                    "duration_ms": s.duration_ms,
                    "transition": s.transition,
                }
                for s in sequence.shots
            ],
            "is_playing": sequence.is_playing,
            "play_count": sequence.play_count,
        }
    
    def get_queue_size(self) -> int:
        """Get number of replays in queue"""
        return len(self.sequence_queue)
    
    def get_next_replay(self) -> Optional[ReplaySequence]:
        """Get next replay from queue"""
        if self.sequence_queue:
            return self.sequence_queue.popleft()
        return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Get replay director statistics"""
        return {
            "total_sequences": len(self.sequences),
            "queue_size": self.get_queue_size(),
            "active_replay": self.active_sequence.sequence_id if self.active_sequence else None,
        }
    
    def reset(self) -> None:
        """Reset replay director"""
        self.sequences.clear()
        self.sequence_queue.clear()
        self.active_sequence = None
        self._log.info("Replay director reset")