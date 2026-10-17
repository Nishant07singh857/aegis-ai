# backend/app/agents/highlight_agents/montage_generator.py
"""
AEGIS AI - Montage Generator
Automatically creates highlight montages from multiple clips.
Adds music synchronization, transitions, and effects.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.montage")

class MontageStyle(str, Enum):
    """Montage editing styles"""
    HYPE = "hype"               # Fast-paced, high energy
    CINEMATIC = "cinematic"     # Slow, dramatic
    CHRONOLOGICAL = "chronological"  # In order of occurrence
    THEMATIC = "thematic"       # Grouped by theme
    PLAYER_SPOTLIGHT = "player_spotlight"  # Focus on one player

class MusicGenre(str, Enum):
    """Music genres for montages"""
    PHONK = "phonk"            # Popular for BGMI edits
    TRAP = "trap"
    ORCHESTRAL = "orchestral"
    ELECTRONIC = "electronic"
    HIP_HOP = "hip_hop"
    ROCK = "rock"

@dataclass
class MontageSegment:
    """A segment in a montage"""
    segment_id: str
    clip_id: str
    start_offset_ms: float = 0.0
    duration_ms: float = 5000.0
    transition: str = "cut"
    effect: str = "none"
    speed: float = 1.0
    audio_ducking: bool = False

@dataclass
class Montage:
    """Complete montage"""
    montage_id: str
    title: str
    style: MontageStyle = MontageStyle.HYPE
    
    # Segments
    segments: List[MontageSegment] = field(default_factory=list)
    total_duration_ms: float = 0.0
    
    # Music
    music_genre: MusicGenre = MusicGenre.PHONK
    music_bpm: int = 140
    beat_synced: bool = True
    
    # Production
    intro_text: str = ""
    outro_text: str = ""
    watermark: str = "AEGIS AI"
    
    # Clips used
    source_clips: List[str] = field(default_factory=list)
    
    # Quality
    excitement_curve: List[float] = field(default_factory=list)
    pacing_score: float = 0.0
    
    created_at: float = field(default_factory=time.time)


class MontageGenerator(BaseAgent):
    """
    Generates highlight montages automatically.
    
    Creates:
    - Hype montages (fast cuts, beat sync)
    - Player spotlight montages
    - Tournament recap montages
    - Themed montages (best clutches, best snipes)
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.montages: Dict[str, Montage] = {}
        self.montage_history: deque = deque(maxlen=50)
        
        # Transition effects
        self.transitions = ["cut", "fade", "dissolve", "wipe", "zoom", "spin", "glitch"]
        
        # Visual effects
        self.effects = [
            "none", "slow_motion", "speed_ramp", "color_grade",
            "vignette", "shake", "pulse", "flash", "chromatic",
        ]
        
        # Beat matching thresholds
        self.beat_match_tolerance_ms = 50  # How close cuts should be to beat
        
        self._log = logger.bind(component="montage_generator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MONTAGE CREATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def generate_montage(
        self,
        clips: List[Dict[str, Any]],
        style: MontageStyle = MontageStyle.HYPE,
        music_genre: MusicGenre = MusicGenre.PHONK,
        title: str = "",
        target_duration_ms: float = 60000.0,
    ) -> Montage:
        """Generate a montage from clips"""
        
        montage = Montage(
            montage_id=f"montage_{int(time.time())}",
            title=title or f"{style.value.title()} Montage",
            style=style,
            music_genre=music_genre,
            source_clips=[c.get("clip_id", "") for c in clips],
        )
        
        if not clips:
            self.montages[montage.montage_id] = montage
            return montage
        
        # Calculate segment durations
        clips_count = len(clips)
        avg_duration = target_duration_ms / clips_count
        
        # Sort clips by excitement for hype montages
        if style == MontageStyle.HYPE:
            clips.sort(key=lambda c: c.get("excitement_score", 0), reverse=True)
        elif style == MontageStyle.CHRONOLOGICAL:
            clips.sort(key=lambda c: c.get("timestamp_ms", 0))
        
        # Create segments
        total_duration = 0.0
        beat_interval_ms = (60 / montage.music_bpm) * 1000  # ms per beat
        
        for i, clip in enumerate(clips):
            # Determine segment duration (aligned to beats)
            base_duration = avg_duration
            
            # More exciting clips get more time
            excitement = clip.get("excitement_score", 50)
            duration_multiplier = 0.5 + (excitement / 100)
            
            # Align to nearest beat
            raw_duration = base_duration * duration_multiplier
            beats = round(raw_duration / beat_interval_ms)
            segment_duration = max(beat_interval_ms, beats * beat_interval_ms)
            
            # Select transition
            if i == 0:
                transition = "fade"
            else:
                transition = self._select_transition(i, clips_count, style)
            
            # Select effect
            effect = self._select_effect(i, clip, style)
            
            # Select speed
            speed = self._select_speed(clip, style)
            
            segment = MontageSegment(
                segment_id=f"seg_{montage.montage_id}_{i}",
                clip_id=clip.get("clip_id", ""),
                start_offset_ms=total_duration,
                duration_ms=segment_duration,
                transition=transition,
                effect=effect,
                speed=speed,
                audio_ducking=(i == clips_count // 2),  # Duck audio at midpoint
            )
            
            montage.segments.append(segment)
            montage.excitement_curve.append(excitement)
            total_duration += segment_duration
        
        montage.total_duration_ms = total_duration
        
        # Generate intro/outro
        montage.intro_text = montage.title
        montage.outro_text = f"Created by {montage.watermark}"
        
        # Calculate pacing score
        montage.pacing_score = self._calculate_pacing(montage)
        
        self.montages[montage.montage_id] = montage
        self.montage_history.append(montage)
        
        self._log.info(
            f"Montage generated: {montage.title}",
            segments=len(montage.segments),
            duration=round(total_duration / 1000, 1),
        )
        
        return montage
    
    def generate_player_montage(
        self,
        player_name: str,
        player_clips: List[Dict[str, Any]],
        target_duration_ms: float = 45000.0,
    ) -> Montage:
        """Generate player spotlight montage"""
        
        # Sort by excitement
        player_clips.sort(key=lambda c: c.get("excitement_score", 0), reverse=True)
        
        # Take top clips
        top_clips = player_clips[:8]
        
        montage = self.generate_montage(
            clips=top_clips,
            style=MontageStyle.PLAYER_SPOTLIGHT,
            music_genre=MusicGenre.PHONK,
            title=f"{player_name} - Highlight Reel",
            target_duration_ms=target_duration_ms,
        )
        
        return montage
    
    def generate_tournament_montage(
        self,
        tournament_name: str,
        all_clips: List[Dict[str, Any]],
    ) -> Montage:
        """Generate tournament recap montage"""
        
        # Take most exciting clips
        sorted_clips = sorted(
            all_clips,
            key=lambda c: c.get("excitement_score", 0),
            reverse=True,
        )[:15]
        
        montage = self.generate_montage(
            clips=sorted_clips,
            style=MontageStyle.CHRONOLOGICAL,
            music_genre=MusicGenre.ORCHESTRAL,
            title=f"{tournament_name} - Best Moments",
            target_duration_ms=120000.0,  # 2 minutes
        )
        
        return montage
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SELECTION HELPERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _select_transition(self, index: int, total: int, style: MontageStyle) -> str:
        """Select appropriate transition"""
        
        import random
        
        if style == MontageStyle.HYPE:
            # Fast transitions for hype
            return random.choice(["cut", "wipe", "zoom", "spin"])
        elif style == MontageStyle.CINEMATIC:
            # Smooth transitions for cinematic
            return random.choice(["fade", "dissolve", "cut"])
        else:
            return random.choice(["cut", "dissolve", "fade"])
    
    def _select_effect(
        self,
        index: int,
        clip: Dict[str, Any],
        style: MontageStyle,
    ) -> str:
        """Select visual effect for segment"""
        
        excitement = clip.get("excitement_score", 50)
        
        if excitement >= 80:
            return "slow_motion"  # Best moments in slow-mo
        elif excitement >= 60:
            return "speed_ramp"
        elif style == MontageStyle.HYPE:
            import random
            return random.choice(["pulse", "flash", "vignette"])
        
        return "none"
    
    def _select_speed(self, clip: Dict[str, Any], style: MontageStyle) -> float:
        """Select playback speed"""
        
        excitement = clip.get("excitement_score", 50)
        
        if excitement >= 85 and style == MontageStyle.CINEMATIC:
            return 0.5  # Half speed for epic moments
        elif excitement >= 70:
            return 0.75
        elif style == MontageStyle.HYPE:
            return 1.0
        
        return 1.0
    
    def _calculate_pacing(self, montage: Montage) -> float:
        """Calculate montage pacing score"""
        
        if not montage.excitement_curve:
            return 0.0
        
        # Good pacing: excitement builds over time
        excitement = montage.excitement_curve
        
        # Check if excitement generally increases
        first_half = sum(excitement[:len(excitement)//2]) / max(1, len(excitement)//2)
        second_half = sum(excitement[len(excitement)//2:]) / max(1, len(excitement) - len(excitement)//2)
        
        buildup = second_half - first_half
        
        # Variance should not be too high (consistency)
        mean = sum(excitement) / len(excitement)
        variance = sum((e - mean)**2 for e in excitement) / len(excitement)
        
        pacing = 50 + buildup * 0.5 - variance * 0.3
        
        return max(0, min(100, pacing))
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_montage(self, montage_id: str) -> Optional[Dict[str, Any]]:
        """Get montage details"""
        
        montage = self.montages.get(montage_id)
        if not montage:
            return None
        
        return {
            "montage_id": montage.montage_id,
            "title": montage.title,
            "style": montage.style.value,
            "segments": len(montage.segments),
            "total_duration_ms": montage.total_duration_ms,
            "music": {
                "genre": montage.music_genre.value,
                "bpm": montage.music_bpm,
            },
            "pacing_score": round(montage.pacing_score, 1),
            "source_clips": len(montage.source_clips),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get montage generator statistics"""
        return {
            "total_montages": len(self.montages),
            "by_style": {
                s.value: len([m for m in self.montages.values() if m.style == s])
                for s in MontageStyle
            },
        }
    
    def reset(self) -> None:
        """Reset montage generator"""
        self.montages.clear()
        self.montage_history.clear()
        self._log.info("Montage generator reset")

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
