# backend/app/agents/narrative_agents/documentary_generator.py
"""
AEGIS AI - Documentary Generator
Automatically generates documentary-style scripts from match events,
tournament storylines, and player narratives.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.documentary")

class DocumentaryStyle(str, Enum):
    """Documentary styles"""
    CINEMATIC = "cinematic"       # Dramatic, movie-like
    BEHIND_THE_SCENES = "bts"    # Insider perspective
    ANALYTICAL = "analytical"    # Deep analysis
    EMOTIONAL = "emotional"      # Feels-driven
    HIGHLIGHT_REEL = "highlight" # Action-focused

class SceneType(str, Enum):
    """Types of documentary scenes"""
    OPENING = "opening"
    BUILDUP = "buildup"
    ACTION = "action"
    INTERVIEW = "interview"
    SLOW_MOTION = "slow_motion"
    MONTAGE = "montage"
    CLIMAX = "climax"
    RESOLUTION = "resolution"
    CREDITS = "credits"

@dataclass
class DocumentaryScene:
    """A single scene in a documentary"""
    scene_id: str
    scene_type: SceneType
    title: str = ""
    
    # Visual description
    visual_description: str = ""
    suggested_shots: List[str] = field(default_factory=list)
    
    # Audio
    narration: str = ""
    music_mood: str = "dramatic"
    sound_effects: List[str] = field(default_factory=list)
    
    # Timing
    duration_seconds: float = 10.0
    transition: str = "fade"
    
    # Content
    context: Dict[str, Any] = field(default_factory=dict)

@dataclass
class DocumentaryScript:
    """Complete documentary script"""
    script_id: str
    title: str
    style: DocumentaryStyle = DocumentaryStyle.CINEMATIC
    
    # Structure
    scenes: List[DocumentaryScene] = field(default_factory=list)
    total_duration: float = 0.0
    
    # Subject
    primary_team: str = ""
    primary_player: str = ""
    tournament_name: str = ""
    
    # Narrative
    opening_hook: str = ""
    central_theme: str = ""
    closing_message: str = ""
    
    # Production
    created_at: float = field(default_factory=time.time)
    target_duration: float = 180.0  # 3 minutes default


class DocumentaryGenerator(BaseAgent):
    """
    Generates documentary-style scripts automatically.
    
    Creates scripts for:
    - Tournament recaps
    - Player spotlights
    - Team journeys
    - Rivalry documentaries
    - Match highlights with narrative
    - Season reviews
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        self.scripts: deque = deque(maxlen=50)
        
        # Scene templates
        self.scene_templates = {
            SceneType.OPENING: {
                "narration_template": "In the world of competitive BGMI, legends are forged in the heat of battle...",
                "music": "epic_orchestral",
                "duration": 15.0,
            },
            SceneType.BUILDUP: {
                "narration_template": "{team} entered {tournament} with {expectation}...",
                "music": "building_tension",
                "duration": 20.0,
            },
            SceneType.ACTION: {
                "narration_template": "And then, everything changed...",
                "music": "intense_action",
                "duration": 12.0,
            },
            SceneType.CLIMAX: {
                "narration_template": "This was the moment that defined everything...",
                "music": "climactic",
                "duration": 15.0,
            },
            SceneType.RESOLUTION: {
                "narration_template": "And so, {team} proved that {moral}...",
                "music": "emotional_resolution",
                "duration": 20.0,
            },
        }
        
        self._log = logger.bind(component="documentary_generator")
    
    def generate_match_documentary(
        self,
        match_id: str,
        match_data: Dict[str, Any],
        style: DocumentaryStyle = DocumentaryStyle.CINEMATIC,
    ) -> DocumentaryScript:
        """Generate a documentary script for a match"""
        
        script = DocumentaryScript(
            script_id=f"doc_{match_id}_{int(time.time())}",
            title=f"{match_data.get('tournament_name', '')} - {match_data.get('match_name', 'Match Recap')}",
            style=style,
            primary_team=match_data.get("winner_team", ""),
            tournament_name=match_data.get("tournament_name", ""),
            target_duration=120.0,
        )
        
        # Opening scene
        script.scenes.append(self._create_opening_scene(match_data))
        
        # Build-up scene
        script.scenes.append(self._create_buildup_scene(match_data))
        
        # Action scenes (key moments)
        key_moments = match_data.get("key_moments", [])
        for moment in key_moments[:5]:
            script.scenes.append(self._create_action_scene(moment))
        
        # Climax scene
        script.scenes.append(self._create_climax_scene(match_data))
        
        # Resolution scene
        script.scenes.append(self._create_resolution_scene(match_data))
        
        # Calculate total duration
        script.total_duration = sum(s.duration_seconds for s in script.scenes)
        
        # Generate narrative
        script.opening_hook = f"Witness the incredible journey of {match_data.get('winner_team', 'a champion')}..."
        script.central_theme = match_data.get("narrative_theme", "Determination and skill")
        script.closing_message = f"{match_data.get('winner_team', 'The champions')} showed us why they are the best."
        
        self.scripts.append(script)
        
        self._log.info(f"Documentary generated: {script.title}")
        
        return script
    
    def generate_player_spotlight(
        self,
        player_id: str,
        player_data: Dict[str, Any],
        highlights: List[Dict[str, Any]],
    ) -> DocumentaryScript:
        """Generate a player spotlight documentary"""
        
        player_name = player_data.get("player_name", "The Player")
        
        script = DocumentaryScript(
            script_id=f"player_doc_{player_id}_{int(time.time())}",
            title=f"{player_name}: A Star is Born",
            style=DocumentaryStyle.EMOTIONAL,
            primary_player=player_name,
            primary_team=player_data.get("team_name", ""),
            target_duration=90.0,
        )
        
        # Opening
        script.scenes.append(DocumentaryScene(
            scene_id="opening",
            scene_type=SceneType.OPENING,
            title=f"Introducing {player_name}",
            narration=f"Some players are born for greatness. {player_name} is one of them.",
            visual_description=f"Slow motion close-up of {player_name}",
            suggested_shots=["player_face_closeup", "slow_motion_walk"],
            music_mood="inspirational",
            duration_seconds=10.0,
        ))
        
        # Background
        script.scenes.append(DocumentaryScene(
            scene_id="background",
            scene_type=SceneType.BUILDUP,
            title="The Beginning",
            narration=f"From {player_data.get('origin', 'humble beginnings')}, {player_name} discovered their passion for BGMI...",
            visual_description="Montage of early gameplay footage",
            duration_seconds=15.0,
        ))
        
        # Highlights
        for i, highlight in enumerate(highlights[:3]):
            script.scenes.append(DocumentaryScene(
                scene_id=f"highlight_{i}",
                scene_type=SceneType.ACTION,
                title=highlight.get("title", "Highlight"),
                narration=highlight.get("narration", ""),
                visual_description=highlight.get("description", ""),
                duration_seconds=10.0,
            ))
        
        # Closing
        script.scenes.append(DocumentaryScene(
            scene_id="closing",
            scene_type=SceneType.RESOLUTION,
            title="The Future",
            narration=f"{player_name} continues to push the boundaries of what's possible in BGMI...",
            music_mood="hopeful",
            duration_seconds=15.0,
        ))
        
        script.total_duration = sum(s.duration_seconds for s in script.scenes)
        script.central_theme = "Rise to greatness"
        
        self.scripts.append(script)
        
        return script
    
    def _create_opening_scene(self, data: Dict[str, Any]) -> DocumentaryScene:
        """Create opening scene"""
        template = self.scene_templates.get(SceneType.OPENING, {})
        
        return DocumentaryScene(
            scene_id="opening",
            scene_type=SceneType.OPENING,
            title="Opening",
            narration=template.get("narration_template", "").format(**data),
            visual_description=f"Drone shot of {data.get('map_name', 'the battlefield')}",
            suggested_shots=["drone_overview", "map_wide_shot"],
            music_mood=template.get("music", "epic"),
            duration_seconds=template.get("duration", 15.0),
            transition="fade_in",
        )
    
    def _create_buildup_scene(self, data: Dict[str, Any]) -> DocumentaryScene:
        """Create buildup scene"""
        return DocumentaryScene(
            scene_id="buildup",
            scene_type=SceneType.BUILDUP,
            title="The Stage is Set",
            narration=f"The {data.get('tournament_name', 'tournament')} was about to witness something extraordinary...",
            visual_description="Teams preparing, crowd atmosphere",
            suggested_shots=["team_huddle", "crowd_reaction", "player_focus"],
            music_mood="building_tension",
            duration_seconds=20.0,
        )
    
    def _create_action_scene(self, moment: Dict[str, Any]) -> DocumentaryScene:
        """Create action scene from a key moment"""
        return DocumentaryScene(
            scene_id=f"action_{moment.get('id', 'unknown')}",
            scene_type=SceneType.ACTION,
            title=moment.get("title", "Key Moment"),
            narration=moment.get("narration", ""),
            visual_description=moment.get("description", ""),
            suggested_shots=["action_replay", "slow_motion"],
            music_mood="intense_action",
            duration_seconds=12.0,
            sound_effects=moment.get("sound_effects", []),
        )
    
    def _create_climax_scene(self, data: Dict[str, Any]) -> DocumentaryScene:
        """Create climax scene"""
        return DocumentaryScene(
            scene_id="climax",
            scene_type=SceneType.CLIMAX,
            title="The Decisive Moment",
            narration="And then, in a moment that will echo through esports history...",
            visual_description="The winning moment in slow motion",
            suggested_shots=["winning_moment", "slow_motion", "reaction_shot"],
            music_mood="climactic",
            duration_seconds=15.0,
        )
    
    def _create_resolution_scene(self, data: Dict[str, Any]) -> DocumentaryScene:
        """Create resolution scene"""
        return DocumentaryScene(
            scene_id="resolution",
            scene_type=SceneType.RESOLUTION,
            title="Victory",
            narration=f"{data.get('winner_team', 'The champions')} stood victorious, having proven their mastery...",
            visual_description="Celebration, trophy lift, team embrace",
            suggested_shots=["celebration", "trophy", "team_photo"],
            music_mood="emotional_resolution",
            duration_seconds=20.0,
            transition="fade_out",
        )
    
    def get_latest_script(self) -> Optional[Dict[str, Any]]:
        """Get latest documentary script"""
        if not self.scripts:
            return None
        
        script = self.scripts[-1]
        return {
            "title": script.title,
            "style": script.style.value,
            "scenes": len(script.scenes),
            "total_duration": round(script.total_duration, 0),
            "primary_team": script.primary_team,
            "primary_player": script.primary_player,
            "opening_hook": script.opening_hook,
            "central_theme": script.central_theme,
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get documentary generator statistics"""
        return {
            "scripts_generated": len(self.scripts),
            "latest": self.get_latest_script(),
        }
    
    def reset(self) -> None:
        """Reset documentary generator"""
        self.scripts.clear()
        self._log.info("Documentary generator reset")

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
