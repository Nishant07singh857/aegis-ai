# backend/app/agents/commentary_agents/script_generator.py
"""
AEGIS AI - Commentary Script Generator
Generates human-like esports commentary using LLM prompts.
Handles multiple commentary styles, context-aware generation,
and produces natural-sounding broadcast commentary.
"""

import asyncio
import time
import random
import hashlib
from typing import Dict, List, Optional, Any, Deque
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.commentary")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & CONSTANTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class CommentaryStyle(str, Enum):
    """Commentary broadcasting styles"""
    HYPE = "hype"              # Energetic, fast-paced, exciting
    ANALYTICAL = "analytical"  # Tactical breakdown, informative
    HINDI = "hindi"           # Pure Hindi esports style
    HINGLISH = "hinglish"     # Mixed Hindi-English (most popular)
    CINEMATIC = "cinematic"   # Documentary-style narration
    CASUAL = "casual"         # Relaxed, conversational
    PLAY_BY_PLAY = "play_by_play"  # Action-focused calling
    COLOR = "color"           # Analysis between action

class CommentaryTone(str, Enum):
    """Emotional tone of commentary"""
    EXCITED = "excited"           # High energy
    TENSE = "tense"              # Suspenseful
    ANALYTICAL = "analytical"    # Thoughtful
    CELEBRATORY = "celebratory"  # Winning moment
    SHOCKED = "shocked"         # Surprised
    CASUAL = "casual"           # Relaxed
    DRAMATIC = "dramatic"       # Building tension
    RESPECTFUL = "respectful"   # Acknowledging skill
    DISAPPOINTED = "disappointed"  # Lost opportunity
    HOPEFUL = "hopeful"         # Comeback potential

class CommentaryTrigger(str, Enum):
    """Events that trigger commentary"""
    KILL = "kill"
    MULTI_KILL = "multi_kill"        # 2+ kills quickly
    TEAM_WIPE = "team_wipe"          # Squad eliminated
    KNOCK = "knock"
    CLUTCH_START = "clutch_start"    # 1vX situation begins
    CLUTCH_PROGRESS = "clutch_progress"  # Getting kills
    CLUTCH_WIN = "clutch_win"        # Clutch successful
    CLUTCH_FAIL = "clutch_fail"      # Clutch failed
    FIGHT_START = "fight_start"
    FIGHT_INTENSE = "fight_intense"  # Peak fight moment
    FIGHT_END = "fight_end"
    ZONE_SHRINK = "zone_shrink"      # Zone closing
    FINAL_CIRCLE = "final_circle"    # Last zone
    MATCH_POINT = "match_point"      # Decisive moment
    COMEBACK = "comeback"
    RIVALRY_MOMENT = "rivalry_moment"
    GRENADE_KILL = "grenade_kill"
    LONG_RANGE_KILL = "long_range_kill"
    HEADSHOT = "headshot"
    FIRST_BLOOD = "first_blood"
    MATCH_START = "match_start"
    MATCH_END = "match_end"
    WINNER_ANNOUNCED = "winner_announced"
    INTERESTING_MOMENT = "interesting_moment"

@dataclass
class CommentaryLine:
    """A single generated commentary line"""
    line_id: str = field(default_factory=lambda: hashlib.md5(str(time.time()).encode()).hexdigest()[:12])
    text: str = ""
    language: str = "hinglish"
    style: CommentaryStyle = CommentaryStyle.HYPE
    tone: CommentaryTone = CommentaryTone.EXCITED
    trigger: Optional[CommentaryTrigger] = None
    
    # Timing
    duration_estimate: float = 3.0  # Seconds to speak
    pause_before: float = 0.2       # Pause before this line
    pause_after: float = 0.3        # Pause after this line
    
    # Priority
    priority: int = 50              # Higher = more important
    can_interrupt: bool = False     # Can interrupt current line
    
    # Context
    context: Dict[str, Any] = field(default_factory=dict)
    related_event_id: Optional[str] = None
    
    # Quality
    confidence: float = 0.8         # LLM confidence
    novelty_score: float = 0.5      # How unique (vs repeated phrases)
    
    # Metadata
    generated_at: float = field(default_factory=time.time)
    llm_model: str = ""
    tokens_used: int = 0
    # Honest provenance label. "heuristic-template" means the text came from
    # human-written template pools (NOT AI). Any other value names the real
    # provider/model, e.g. "ollama:llama3.1". Never mislabel this.
    generation_source: str = "heuristic-template"

@dataclass
class CommentaryBuffer:
    """Buffer for managing commentary flow"""
    lines: Deque[CommentaryLine] = field(default_factory=lambda: deque(maxlen=50))
    current_line: Optional[CommentaryLine] = None
    last_spoken_time: float = 0.0
    silence_duration: float = 0.0
    max_silence: float = 8.0        # Max silence before filler needed
    min_gap_between: float = 0.5    # Minimum gap between lines
    total_lines_generated: int = 0


class ScriptGenerator(BaseAgent):
    """
    Generates commentary scripts via the zero-cost AI layer
    (backend/app/ai): a local LLM (default Ollama) when one is reachable,
    otherwise honest template pools labelled source="heuristic-template".

    Features:
    - Multiple commentary styles (Hype, Analytical, Hindi, Hinglish, Cinematic)
    - Context-aware generation using match state
    - Event-triggered commentary
    - Natural language variation (not repetitive)
    - Priority-based queuing
    - Filler commentary for silence
    - Player/team knowledge integration
    - Rivalry and storyline awareness
    
    The generator uses prompt engineering with few-shot examples
    to produce natural-sounding esports commentary that matches
    professional BGMI casters.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Commentary buffer
        self.buffer = CommentaryBuffer()
        
        # Active style
        self.active_style = CommentaryStyle.HINGLISH
        self.active_tone = CommentaryTone.CASUAL
        
        # Prompt templates cache
        self.prompt_templates: Dict[str, str] = {}
        self.few_shot_examples: Dict[str, List[Dict[str, str]]] = {}
        
        # Context tracking
        self.last_trigger_time: Dict[str, float] = {}
        self.recent_topics: Deque[str] = deque(maxlen=20)
        
        # Load prompt templates
        self._load_prompt_templates()
        self._load_few_shot_examples()
        
        # Configuration
        self.max_tokens_per_line = 100
        self.temperature_default = 0.8
        self.min_confidence = 0.6

        # Zero-cost AI layer (backend/app/ai). Resolved lazily on first use
        # so importing this module never triggers provider selection.
        self._llm_provider = None
        self._llm_provider_failed = False
        # Provenance of the most recent _call_llm output (honest labelling).
        self._last_generation_source = "heuristic-template"
        self._last_generation_was_llm = False

        self._log = logger.bind(component="script_generator")
    
    def _load_prompt_templates(self) -> None:
        """Load commentary prompt templates"""
        
        self.prompt_templates = {
            "kill": """
You are a professional BGMI esports caster. Generate {style} commentary for this moment:

Event: {player_name} from {team_name} eliminated {victim_name} from {victim_team}
Weapon: {weapon}
Distance: {distance}m
Headshot: {headshot}
Zone Phase: {zone_phase}

Current match context:
- Teams alive: {teams_alive}
- {player_name} has {player_kills} kills this match
- Fight intensity: {intensity}

Generate ONE line of {language} commentary that sounds natural and exciting.
Keep it under 15 words. Be energetic but authentic.
Do NOT repeat common phrases. Be creative.
""",
            
            "multi_kill": """
You are a professional BGMI esports caster. Generate {style} commentary:

{player_name} from {team_name} just got a MULTI KILL!
Kills in quick succession: {kill_count}
Weapons used: {weapons}
Time span: {time_span} seconds

This is a {intensity} moment. Generate {language} commentary that builds excitement.
Use escalating energy. Make it memorable.
""",
            
            "team_wipe": """
You are a professional BGMI esports caster. Generate {style} commentary:

TEAM WIPE! {team_name} has been eliminated by {eliminator_team}!
{team_name} finishes at #{placement} place.
They had {team_kills} kills in this match.

Generate {language} commentary that gives respect to the fallen team
while celebrating the eliminator's achievement.
""",
            
            "clutch_start": """
You are a professional BGMI esports caster. Generate {style} commentary:

CLUTCH SITUATION! {player_name} from {team_name} is alone against {enemy_count} enemies!
Win probability: {probability}%
{player_name} has {hp} HP and {armor} armor.
Weapons: {weapons}

This is a {difficulty} clutch situation. Generate {language} commentary
that captures the tension and stakes of this moment.
Build drama. Make viewers feel the pressure.
""",
            
            "clutch_win": """
You are a professional BGMI esports caster. Generate {style} commentary:

CLUTCH COMPLETE! {player_name} WON a {clutch_type} against all odds!
Win probability was only {probability}%!
They eliminated {enemies_killed} enemies.

This is a legendary moment! Generate {language} commentary
with maximum hype and celebration. This is what esports is about!
""",
            
            "fight_intense": """
You are a professional BGMI esports caster. Generate {style} commentary:

Intense fight between {team_a} and {team_b}!
Fight duration: {duration}s
Total damage: {damage}
Kills so far: {kills}
Fight intensity: {intensity}/100

Generate fast-paced {language} play-by-play commentary.
Describe the action as if you're watching it live.
Keep it urgent and exciting.
""",
            
            "final_circle": """
You are a professional BGMI esports caster. Generate {style} commentary:

FINAL CIRCLE! Only {teams_alive} teams remaining!
Teams: {team_list}
Zone is closing in {zone_time} seconds.

This is the most critical moment of the match.
Generate {language} commentary that captures the ultimate tension.
Every decision matters now.
""",
            
            "filler": """
You are a professional BGMI esports caster. Generate {style} commentary:

Current state: {match_state}
Teams alive: {teams_alive}
Zone phase: {zone_phase}
Notable: {notable_info}

Generate a brief {language} filler commentary line.
Keep it informative but casual. Bridge the silence naturally.
""",
        }
        
        self._log.info(f"Loaded {len(self.prompt_templates)} prompt templates")
    
    def _load_few_shot_examples(self) -> None:
        """Load few-shot examples for better LLM output"""
        
        self.few_shot_examples = {
            "hype": [
                {"input": "Kill with M416", "output": "OH MY GOD! What a clean elimination with the M416! Absolutely clinical!"},
                {"input": "Clutch 1v3", "output": "THIS IS UNBELIEVABLE! He's taking them down one by one! The crowd is on their feet!"},
                {"input": "Team wipe", "output": "AND THEY'RE GONE! Complete squad wipe! What a statement by this team!"},
            ],
            "analytical": [
                {"input": "Kill with M416", "output": "Excellent spray control there with the M416. Notice how he managed the recoil perfectly at that range."},
                {"input": "Rotation", "output": "Smart rotation decision - they're reading the zone perfectly and positioning for the next phase."},
            ],
            "hinglish": [
                {"input": "Kill", "output": "BHAI! Kya laser beam spray tha! Ekdum perfect M416 control, no chance for opponent!"},
                {"input": "Clutch", "output": "ARE BHAI! Yeh kya dekh liya! Absolute cinema! 1v3 clutch in final circle!"},
                {"input": "Team wipe", "output": "GAYE! Poori squad wipe! Yeh team toh destroyer nikli aaj!"},
            ],
            "hindi": [
                {"input": "Kill", "output": "क्या शानदार elimination! बिल्कुल perfect aim, एकदम सटीक निशाना!"},
                {"input": "Clutch", "output": "ये तो कमाल हो गया! अकेला खिलाड़ी पूरी squad पर भारी!"},
            ],
        }
        
        self._log.info(f"Loaded few-shot examples for {len(self.few_shot_examples)} styles")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMENTARY GENERATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def generate_commentary(
        self,
        trigger: CommentaryTrigger,
        context: Dict[str, Any],
        style: Optional[CommentaryStyle] = None,
        tone: Optional[CommentaryTone] = None,
        language: str = "hinglish",
    ) -> Optional[CommentaryLine]:
        """
        Generate a commentary line for a specific trigger event.
        
        Args:
            trigger: What triggered this commentary
            context: Match context data
            style: Override commentary style
            tone: Override emotional tone
            language: Language to generate in
            
        Returns:
            CommentaryLine or None if generation failed
        """
        
        # Check cooldown for this trigger type
        if self._is_in_cooldown(trigger.value):
            return None
        
        # Get appropriate prompt template
        template_key = self._get_template_key(trigger)
        template = self.prompt_templates.get(template_key)
        
        if not template:
            template = self.prompt_templates.get("filler", "")
            trigger = CommentaryTrigger.INTERESTING_MOMENT
        
        # Determine style and tone
        style = style or self.active_style
        tone = tone or self._determine_tone(trigger, context)
        
        # Fill template with context
        prompt = template.format(
            style=style.value,
            language=language,
            **context,
        )
        
        # Add few-shot examples
        examples = self.few_shot_examples.get(style.value, [])
        if examples:
            example_text = "\n\nExamples of good commentary:\n"
            for ex in examples[:2]:
                example_text += f"Input: {ex['input']}\nOutput: {ex['output']}\n"
            prompt += example_text
        
        # Generate via the zero-cost AI layer (LLM provider, else honest templates)
        text = await self._call_llm(prompt, style, tone)

        if not text or len(text) < 5:
            return None

        # Create commentary line. Provenance is recorded honestly:
        # generation_source is "heuristic-template" for template output
        # (NOT AI) or the real provider label, e.g. "ollama:llama3.1".
        line = CommentaryLine(
            text=text,
            language=language,
            style=style,
            tone=tone,
            trigger=trigger,
            priority=self._get_trigger_priority(trigger),
            context=context,
            duration_estimate=self._estimate_duration(text),
            generation_source=self._last_generation_source,
            llm_model=self._last_generation_source
            if self._last_generation_was_llm
            else "",
            confidence=0.85 if self._last_generation_was_llm else 0.5,
        )
        
        # Check novelty (avoid repetition)
        if self._is_repetitive(text):
            # Try once more with different prompt
            text = await self._call_llm(prompt + "\nBe more creative and different.", style, tone)
            if text:
                line.text = text
                line.novelty_score = 0.3
        
        # Add to buffer
        self._add_to_buffer(line)
        
        # Update cooldown
        self.last_trigger_time[trigger.value] = time.time()
        
        self._log.debug(
            f"Commentary generated: {trigger.value}",
            style=style.value,
            tone=tone.value,
            language=language,
            length=len(text),
        )
        
        return line
    
    async def generate_filler(
        self,
        context: Dict[str, Any],
        style: Optional[CommentaryStyle] = None,
    ) -> Optional[CommentaryLine]:
        """Generate filler commentary during silence"""
        
        return await self.generate_commentary(
            trigger=CommentaryTrigger.INTERESTING_MOMENT,
            context=context,
            style=style or CommentaryStyle.CASUAL,
            tone=CommentaryTone.CASUAL,
        )
    
    async def generate_match_start(
        self,
        teams: List[Dict[str, str]],
        map_name: str,
        tournament_name: str = "",
    ) -> List[CommentaryLine]:
        """Generate match opening commentary"""
        
        lines = []
        
        # Opening line
        context = {
            "match_state": "starting",
            "teams_alive": 16,
            "zone_phase": 0,
            "notable_info": f"Match starting on {map_name}",
            "map_name": map_name,
            "tournament_name": tournament_name,
            "team_count": len(teams),
        }
        
        opening = await self.generate_commentary(
            trigger=CommentaryTrigger.MATCH_START,
            context=context,
            style=CommentaryStyle.CINEMATIC,
            tone=CommentaryTone.EXCITED,
        )
        
        if opening:
            lines.append(opening)
        
        # Team introductions
        if teams:
            context["notable_info"] = f"Featuring {teams[0].get('team_name', '')}"
            team_intro = await self.generate_commentary(
                trigger=CommentaryTrigger.INTERESTING_MOMENT,
                context=context,
                style=CommentaryStyle.ANALYTICAL,
                tone=CommentaryTone.CASUAL,
            )
            if team_intro:
                lines.append(team_intro)
        
        return lines
    
    async def generate_match_end(
        self,
        winner_team: str,
        winner_players: List[str],
        match_stats: Dict[str, Any],
    ) -> List[CommentaryLine]:
        """Generate match closing commentary"""
        
        lines = []
        
        context = {
            "match_state": "ended",
            "teams_alive": 1,
            "zone_phase": 8,
            "notable_info": f"{winner_team} wins!",
            "winner_team": winner_team,
            "winner_players": ", ".join(winner_players),
            "total_kills": match_stats.get("total_kills", 0),
        }
        
        # Winner announcement
        winner_line = await self.generate_commentary(
            trigger=CommentaryTrigger.WINNER_ANNOUNCED,
            context=context,
            style=CommentaryStyle.HYPE,
            tone=CommentaryTone.CELEBRATORY,
        )
        
        if winner_line:
            lines.append(winner_line)
        
        # MVP mention
        if match_stats.get("mvp"):
            context["notable_info"] = f"MVP: {match_stats['mvp']}"
            mvp_line = await self.generate_commentary(
                trigger=CommentaryTrigger.INTERESTING_MOMENT,
                context=context,
                style=CommentaryStyle.ANALYTICAL,
                tone=CommentaryTone.RESPECTFUL,
            )
            if mvp_line:
                lines.append(mvp_line)
        
        return lines
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LLM INTERACTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_llm_provider(self):
        """
        Lazily resolve the text-generation provider from backend/app/ai.

        The import is deliberately lazy: importing this module must never
        trigger provider selection, network probes, or paid-SDK imports.
        Returns None only if the ai package itself cannot be imported.
        """
        if self._llm_provider is None and not self._llm_provider_failed:
            try:
                from backend.app.ai import get_llm_provider
                self._llm_provider = get_llm_provider()
            except Exception as e:
                self._log.warning(
                    "AI provider layer unavailable; commentary will use "
                    "honest template fallback only",
                    error=str(e),
                )
                self._llm_provider_failed = True
        return self._llm_provider

    async def _call_llm(
        self,
        prompt: str,
        style: CommentaryStyle,
        tone: CommentaryTone,
    ) -> Optional[str]:
        """
        Generate commentary text via the zero-cost AI layer.

        Order:
          1. Active LLM provider (default: local Ollama; falls back to
             heuristic automatically when Ollama is not reachable).
          2. Honest template fallback, routed through HeuristicProvider so
             the output is labelled source="heuristic-template".

        Template output is NEVER presented as AI-generated: the provenance
        is recorded on CommentaryLine.generation_source by the caller.
        No paid API is ever called unless the operator explicitly opted in
        via ENABLE_PAID_LLM=true (see backend/app/ai/optional_paid.py).
        """

        provider = self._get_llm_provider()

        if provider is not None:
            try:
                # Providers are synchronous (blocking I/O); don't stall the
                # event loop while a local model thinks.
                text = await asyncio.to_thread(
                    provider.generate,
                    prompt,
                    max_tokens=self.max_tokens_per_line,
                    temperature=self.temperature_default,
                )
                if text and len(text.strip()) >= 5:
                    self._last_generation_source = provider.last_source()
                    self._last_generation_was_llm = (
                        provider.name != "heuristic-template"
                    )
                    return text.strip()
            except Exception as e:
                # Provider looked available but the request failed
                # (Ollama went away mid-call, model missing, ...).
                # Fall through to the honest template path below.
                self._log.warning(
                    "LLM provider failed; using honest template fallback",
                    provider=getattr(provider, "name", "unknown"),
                    error=str(e),
                )

        # Honest template fallback via HeuristicProvider. The provider
        # records source="heuristic-template" in its own metadata, and we
        # mirror that provenance onto the CommentaryLine below.
        try:
            from backend.app.ai import HeuristicProvider
            heuristic = HeuristicProvider(
                template_fn=lambda p, **kw: self._generate_template_response(
                    style, tone, p
                )
            )
            text = heuristic.generate(prompt)
        except Exception as e:
            self._log.error(f"Template fallback failed: {e}")
            return None

        self._last_generation_source = "heuristic-template"
        self._last_generation_was_llm = False
        return text
    
    def _generate_template_response(
        self,
        style: CommentaryStyle,
        tone: CommentaryTone,
        prompt: str,
    ) -> str:
        """Generate template-based response with variation"""
        
        # Extract key info from prompt
        # (In production, this would be LLM-generated)
        
        responses = {
            CommentaryStyle.HINGLISH: [
                "BHAI! Kya incredible play tha! Absolutely brilliant!",
                "Arey yeh toh kamal kar diya! What a moment!",
                "Dekho dekho! Yeh hai asli BGMI! Pure class!",
                "Kya baat hai! This is why we love esports!",
                "Wah! Perfect execution, perfect timing!",
            ],
            CommentaryStyle.HYPE: [
                "OH MY GOD! WHAT A PLAY! ABSOLUTELY INCREDIBLE!",
                "THIS IS UNBELIEVABLE! I CAN'T BELIEVE WHAT I JUST SAW!",
                "LEGENDARY! THIS WILL GO DOWN IN HISTORY!",
                "WHAT A MOMENT! THE CROWD IS GOING WILD!",
            ],
            CommentaryStyle.ANALYTICAL: [
                "Excellent tactical decision-making on display here.",
                "Notice the positioning and map awareness - absolutely world class.",
                "The game sense required to make that play is remarkable.",
                "This is textbook execution of a well-practiced strategy.",
            ],
            CommentaryStyle.HINDI: [
                "क्या शानदार play! बिल्कुल अद्भुत!",
                "ये है असली BGMI का मज़ा! कमाल का प्रदर्शन!",
                "देखिए कैसे ये खिलाड़ी pressure handle करते हैं!",
            ],
            CommentaryStyle.CINEMATIC: [
                "And in this moment, legends are forged in the fires of competition...",
                "The story of this match takes yet another dramatic turn...",
                "Destiny hangs in the balance as the final moments unfold...",
            ],
            CommentaryStyle.CASUAL: [
                "Nice play there, good to see smart positioning.",
                "That's solid gameplay right there, well executed.",
                "Good decision-making in that situation.",
            ],
        }
        
        style_responses = responses.get(style, responses[CommentaryStyle.HINGLISH])
        
        # Add randomness to avoid repetition
        return random.choice(style_responses)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BUFFER MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _add_to_buffer(self, line: CommentaryLine) -> None:
        """Add line to commentary buffer"""
        
        # Check if this line should interrupt current
        if line.can_interrupt and self.buffer.current_line:
            if line.priority > self.buffer.current_line.priority + 20:
                # Re-queue current line
                self.buffer.lines.appendleft(self.buffer.current_line)
                self.buffer.current_line = line
                return
        
        self.buffer.lines.append(line)
        self.buffer.total_lines_generated += 1
    
    def get_next_line(self) -> Optional[CommentaryLine]:
        """Get the next commentary line to speak"""
        
        # If buffer is empty, return None (trigger filler generation)
        if not self.buffer.lines and not self.buffer.current_line:
            return None
        
        # If currently speaking a line, check if it's done
        if self.buffer.current_line:
            elapsed = time.time() - self.buffer.last_spoken_time
            if elapsed < self.buffer.current_line.duration_estimate:
                return None  # Still speaking
        
        # Get next line from buffer
        if self.buffer.lines:
            self.buffer.current_line = self.buffer.lines.popleft()
            self.buffer.last_spoken_time = time.time()
            return self.buffer.current_line
        
        self.buffer.current_line = None
        return None
    
    def get_silence_duration(self) -> float:
        """Get current silence duration"""
        if self.buffer.last_spoken_time > 0:
            return time.time() - self.buffer.last_spoken_time
        return 0.0
    
    def needs_filler(self) -> bool:
        """Check if filler commentary is needed"""
        return self.get_silence_duration() > self.buffer.max_silence
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # UTILITIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_template_key(self, trigger: CommentaryTrigger) -> str:
        """Get prompt template key for trigger"""
        mapping = {
            CommentaryTrigger.KILL: "kill",
            CommentaryTrigger.MULTI_KILL: "multi_kill",
            CommentaryTrigger.TEAM_WIPE: "team_wipe",
            CommentaryTrigger.CLUTCH_START: "clutch_start",
            CommentaryTrigger.CLUTCH_WIN: "clutch_win",
            CommentaryTrigger.FIGHT_INTENSE: "fight_intense",
            CommentaryTrigger.FINAL_CIRCLE: "final_circle",
            CommentaryTrigger.INTERESTING_MOMENT: "filler",
            CommentaryTrigger.MATCH_START: "filler",
            CommentaryTrigger.MATCH_END: "filler",
        }
        return mapping.get(trigger, "filler")
    
    def _determine_tone(
        self,
        trigger: CommentaryTrigger,
        context: Dict[str, Any],
    ) -> CommentaryTone:
        """Determine appropriate emotional tone"""
        
        tone_mapping = {
            CommentaryTrigger.KILL: CommentaryTone.EXCITED,
            CommentaryTrigger.MULTI_KILL: CommentaryTone.EXCITED,
            CommentaryTrigger.TEAM_WIPE: CommentaryTone.DRAMATIC,
            CommentaryTrigger.CLUTCH_START: CommentaryTone.TENSE,
            CommentaryTrigger.CLUTCH_WIN: CommentaryTone.CELEBRATORY,
            CommentaryTrigger.CLUTCH_FAIL: CommentaryTone.DISAPPOINTED,
            CommentaryTrigger.FIGHT_INTENSE: CommentaryTone.EXCITED,
            CommentaryTrigger.FINAL_CIRCLE: CommentaryTone.TENSE,
            CommentaryTrigger.MATCH_START: CommentaryTone.EXCITED,
            CommentaryTrigger.MATCH_END: CommentaryTone.CELEBRATORY,
            CommentaryTrigger.WINNER_ANNOUNCED: CommentaryTone.CELEBRATORY,
        }
        
        return tone_mapping.get(trigger, CommentaryTone.CASUAL)
    
    def _get_trigger_priority(self, trigger: CommentaryTrigger) -> int:
        """Get priority for trigger type"""
        priorities = {
            CommentaryTrigger.CLUTCH_WIN: 100,
            CommentaryTrigger.CLUTCH_START: 95,
            CommentaryTrigger.TEAM_WIPE: 90,
            CommentaryTrigger.MULTI_KILL: 85,
            CommentaryTrigger.FIGHT_INTENSE: 80,
            CommentaryTrigger.CLUTCH_PROGRESS: 80,
            CommentaryTrigger.FINAL_CIRCLE: 75,
            CommentaryTrigger.KILL: 60,
            CommentaryTrigger.MATCH_END: 90,
            CommentaryTrigger.MATCH_START: 70,
            CommentaryTrigger.FIGHT_START: 65,
            CommentaryTrigger.KNOCK: 50,
            CommentaryTrigger.ZONE_SHRINK: 45,
            CommentaryTrigger.INTERESTING_MOMENT: 20,
        }
        
        return priorities.get(trigger, 30)
    
    def _estimate_duration(self, text: str) -> float:
        """Estimate how long text takes to speak"""
        # Average speaking rate: ~3 words per second
        words = len(text.split())
        return max(2.0, words / 3.0 + 1.0)  # Minimum 2 seconds
    
    def _is_in_cooldown(self, trigger_key: str) -> bool:
        """Check if trigger is in cooldown"""
        last_time = self.last_trigger_time.get(trigger_key, 0)
        cooldowns = {
            "kill": 3.0,
            "multi_kill": 1.0,
            "team_wipe": 2.0,
            "clutch_win": 1.0,
            "fight_intense": 5.0,
            "filler": 8.0,
            "interesting_moment": 8.0,
        }
        cooldown = cooldowns.get(trigger_key, 5.0)
        return (time.time() - last_time) < cooldown
    
    def _is_repetitive(self, text: str) -> bool:
        """Check if text is too similar to recent lines"""
        for recent in list(self.buffer.lines)[-5:]:
            if recent.text.lower() == text.lower():
                return True
            # Check word overlap
            words_a = set(text.lower().split())
            words_b = set(recent.text.lower().split())
            if len(words_a & words_b) / max(len(words_a), len(words_b)) > 0.7:
                return True
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STYLE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_style(self, style: CommentaryStyle) -> None:
        """Set active commentary style"""
        self.active_style = style
        self._log.info(f"Commentary style set: {style.value}")
    
    def set_tone(self, tone: CommentaryTone) -> None:
        """Set active commentary tone"""
        self.active_tone = tone
    
    def get_stats(self) -> Dict[str, Any]:
        """Get script generator statistics"""
        return {
            "total_lines_generated": self.buffer.total_lines_generated,
            "buffer_size": len(self.buffer.lines),
            "active_style": self.active_style.value,
            "silence_duration": round(self.get_silence_duration(), 1),
            "needs_filler": self.needs_filler(),
            "templates_loaded": len(self.prompt_templates),
        }
    
    def reset(self) -> None:
        """Reset script generator"""
        self.buffer = CommentaryBuffer()
        self.last_trigger_time.clear()
        self.recent_topics.clear()
        self._log.info("Script generator reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("fight_detected")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("highlight_moment")
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
