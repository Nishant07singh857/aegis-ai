# backend/app/agents/orchestrator.py
"""
AEGIS AI - Agent Orchestrator

DORMANT IN PRODUCTION — honest status (inspection H5/H32/H33):
``AgentOrchestrator`` is NOT started by the production app.
``app/main.py`` never imports or starts it; its only callers are
``backend/scripts/smoke_agents.py`` and e2e tests. The 41
``BaseAgent`` subclasses are a library, not a running system.

What actually runs in production, via direct instantiation (not via
this orchestrator), is only:

- 6 agent classes: FightDetector, TeamTracker, UtilityTracker,
  StorytellerAgent, VoiceSynthesizer, LanguageManager; plus
- 3 function-modules: the MVP scorer (``compute_mvp_score``),
  win-probability, and the day-reel builder.

Do NOT "fix" dormancy by starting this orchestrator without
reviewing each agent first: parts of this library fabricate data or
consume telemetry shapes that cannot exist under the spectator-only
directive, and this orchestrator no longer constructs the deprecated
second director stack (see the Phase 1 / Phase 4 notes in
``initialize_all_agents``).
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import structlog

from .base_agent import BaseAgent, AgentConfig, AgentPriority
from .agent_registry import AgentRegistry
from ..core.event_bus import EventBus, EventPriority
from ..config import settings

# Import all agent types
from .team_agents import (
    TeamTracker, TeamMemory, RotationAnalyzer,
    UtilityTracker, PositioningAnalyzer,
)
from .fight_agents import (
    FightDetector, IntensityScorer, ClutchDetector, EngagementAnalyzer,
)
from .observer_agents import (
    CinematicController,
)
# NOTE (H33): ObserverDirector (director_ai.py), POVSelector,
# SafetyController and PlaybookManager are intentionally NOT imported
# here. That stack self-declares "SUPERSEDED by live_director, do not
# wire"; this orchestrator must never construct it, or starting the
# orchestrator would run two directors at once.
from .commentary_agents import (
    ScriptGenerator, EmotionEngine, HypeDetector,
    VoiceSynthesizer, CasterMemory, LanguageManager,
)
from .analytics_agents import (
    MVPCalculator, PlayerProfiler, TeamChemistryAnalyzer,
    PerformanceTracker, ScoutingReporter,
)
from .prediction_agents import (
    FightPredictor, SurvivalPredictor, ZonePredictor, WinnerPredictor,
)
from .narrative_agents import (
    StorylineBuilder, RivalryTracker, ComebackDetector, DocumentaryGenerator,
)
from .highlight_agents import (
    ClipDetector, ReplayDirector, MontageGenerator, ContentPublisher,
)
from .referee_agents import (
    RuleEnforcer, CheatingDetector, DisputeResolver,
)

logger = structlog.get_logger("aegis.agents.orchestrator")


@dataclass
class OrchestratorState:
    """Current state of the orchestrator"""
    is_running: bool = False
    match_id: str = ""
    tournament_id: str = ""
    
    # Agent counts
    total_agents: int = 0
    active_agents: int = 0
    
    # Startup phases
    startup_phase: str = "not_started"
    startup_completed: bool = False
    
    # Timing
    started_at: float = 0.0
    uptime_seconds: float = 0.0
    
    # Errors
    startup_errors: List[str] = field(default_factory=list)
    runtime_errors: int = 0


class AgentOrchestrator:
    """
    Dormant orchestrator for the agent library — NOT run in production.

    Production never starts this class (``app/main.py`` does not
    import it). Only 6 agent classes run in production, via direct
    instantiation elsewhere: FightDetector, TeamTracker,
    UtilityTracker, StorytellerAgent, VoiceSynthesizer,
    LanguageManager — plus 3 function-modules (MVP
    ``compute_mvp_score``, win-probability, day-reel). Everything this
    orchestrator would construct beyond that is dormant library code.

    If constructed (smoke script / e2e tests only), it registers:

    - 16 Team Agents (one per team)
    - Fight Detection / Commentary / Analytics / Prediction /
      Narrative / Highlight / Referee agents from the library

    It deliberately does NOT construct the deprecated second director
    stack — ObserverDirector (``director_ai.py``), ``POVSelector``,
    ``SafetyController``, ``PlaybookManager`` — which is superseded by
    ``app/services/live_director`` (inspection H33). The live director
    is the one and only director.

    Startup Order (by priority):
    1. CRITICAL: (none — the deprecated director stack is not built)
    2. HIGH: FightDetector, CommentaryAgents
    3. MEDIUM: TeamAgents (16), AnalyticsAgents
    4. LOW: PredictionAgents, NarrativeAgents, HighlightAgents
    5. BACKGROUND: RefereeAgents, MemoryAgents
    """
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.registry = AgentRegistry(event_bus)
        self.state = OrchestratorState()
        
        # Agent instances
        self.agents: Dict[str, BaseAgent] = {}
        
        # Specialized agent references
        self.team_agents: Dict[str, TeamTracker] = {}
        # Always None (H33): the deprecated ObserverDirector stack is
        # never constructed by this orchestrator. Attribute kept only
        # so existing attribute access (smoke/e2e) reads an honest
        # None instead of an unexpected AttributeError.
        self.observer_director: Optional[Any] = None
        self.fight_coordinator: Optional[FightDetector] = None
        self.commentary_agent: Optional[ScriptGenerator] = None
        
        self._log = logger.bind(component="orchestrator")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INITIALIZATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def initialize_all_agents(
        self,
        teams: List[Dict[str, str]],
        match_id: str = "",
        tournament_id: str = "",
    ) -> None:
        """
        Initialize ALL agents for a match.
        
        Args:
            teams: List of team dicts with 'team_id' and 'team_name'
            match_id: Current match identifier
            tournament_id: Current tournament identifier
        """
        
        self.state.match_id = match_id
        self.state.tournament_id = tournament_id
        self.state.startup_phase = "initializing"
        
        self._log.info(
            f"Initializing agent system for {len(teams)} teams",
            match_id=match_id,
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 1: CRITICAL AGENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_1_critical"
        self._log.info("Phase 1: Initializing CRITICAL agents...")
        
        # H33: the deprecated second director stack is NOT constructed
        # here. ObserverDirector (director_ai.py) and SafetyController
        # self-declare "SUPERSEDED by app/services/live_director, do
        # not wire" — constructing them would let a started
        # orchestrator run two directors at once. The live director
        # (services/live_director) is the only director; this phase
        # therefore registers no CRITICAL agents.
        self._log.info(
            "Phase 1: deprecated director stack intentionally not "
            "constructed (superseded by services/live_director)"
        )

        self._log.info(f"Phase 1 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 2: HIGH PRIORITY AGENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_2_high"
        self._log.info("Phase 2: Initializing HIGH priority agents...")
        
        # Fight Detection Coordinator
        self.fight_coordinator = FightDetector(
            config=self._make_config(
                "fight_coordinator", "fight_coordinator", AgentPriority.HIGH
            ),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "fight_coordinator",
            "fight_coordinator",
            self.fight_coordinator,
            AgentPriority.HIGH,
        )
        
        # Intensity Scorer
        intensity_scorer = IntensityScorer(
            config=self._make_config("intensity_scorer", "intensity_scorer", AgentPriority.HIGH),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "intensity_scorer",
            "intensity_scorer",
            intensity_scorer,
            AgentPriority.HIGH,
        )
        
        # Clutch Detector
        clutch_detector = ClutchDetector(
            config=self._make_config("clutch_detector", "clutch_detector", AgentPriority.HIGH),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "clutch_detector",
            "clutch_detector",
            clutch_detector,
            AgentPriority.HIGH,
        )
        
        # Commentary Agents
        self.commentary_agent = ScriptGenerator(
            config=self._make_config(
                "script_generator", "script_generator", AgentPriority.HIGH
            ),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "script_generator",
            "script_generator",
            self.commentary_agent,
            AgentPriority.HIGH,
        )
        
        emotion_engine = EmotionEngine(
            config=self._make_config("emotion_engine", "emotion_engine", AgentPriority.HIGH),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "emotion_engine",
            "emotion_engine",
            emotion_engine,
            AgentPriority.HIGH,
        )
        
        hype_detector = HypeDetector(
            config=self._make_config("hype_detector", "hype_detector", AgentPriority.HIGH),
            event_bus=self.event_bus,
        )
        self._register_agent(
            "hype_detector",
            "hype_detector",
            hype_detector,
            AgentPriority.HIGH,
        )
        
        self._log.info(f"Phase 2 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 3: TEAM AGENTS (MEDIUM PRIORITY)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_3_team_agents"
        self._log.info(f"Phase 3: Initializing {len(teams)} Team Agents...")
        
        for i, team in enumerate(teams[:settings.MAX_TEAM_AGENTS]):
            team_id = team.get("team_id", f"team_{i+1:03d}")
            team_name = team.get("team_name", f"Team {i+1}")
            short_name = team.get("short_name", team_name[:4].upper())
            player_names = team.get("player_names", [
                f"{team_name}_Player1",
                f"{team_name}_Player2",
                f"{team_name}_Player3",
                f"{team_name}_Player4",
            ])
            
            # Create Team Agent
            team_agent = TeamTracker(
                config=self._make_config(
                    f"team_agent_{team_id}", "team_agent", AgentPriority.MEDIUM
                ),
                event_bus=self.event_bus,
                team_id=team_id,
                team_name=team_name,
                short_name=short_name,
                player_names=player_names,
            )
            
            self._register_agent(
                f"team_agent_{team_id}",
                "team_agent",
                team_agent,
                AgentPriority.MEDIUM,
            )
            
            self.team_agents[team_id] = team_agent
            
            # Per-team analysis agents (previously never instantiated)
            rotation_analyzer = RotationAnalyzer(
                config=self._make_config(
                    f"rotation_analyzer_{team_id}", "rotation_analyzer",
                    AgentPriority.MEDIUM,
                ),
                event_bus=self.event_bus,
                team_id=team_id,
            )
            self._register_agent(
                f"rotation_analyzer_{team_id}",
                "rotation_analyzer",
                rotation_analyzer,
                AgentPriority.MEDIUM,
            )
            
            utility_tracker = UtilityTracker(
                config=self._make_config(
                    f"utility_tracker_{team_id}", "utility_tracker",
                    AgentPriority.MEDIUM,
                ),
                event_bus=self.event_bus,
                team_id=team_id,
            )
            self._register_agent(
                f"utility_tracker_{team_id}",
                "utility_tracker",
                utility_tracker,
                AgentPriority.MEDIUM,
            )
            
            positioning_analyzer = PositioningAnalyzer(
                config=self._make_config(
                    f"positioning_analyzer_{team_id}", "positioning_analyzer",
                    AgentPriority.MEDIUM,
                ),
                event_bus=self.event_bus,
                team_id=team_id,
            )
            self._register_agent(
                f"positioning_analyzer_{team_id}",
                "positioning_analyzer",
                positioning_analyzer,
                AgentPriority.MEDIUM,
            )
        
        self._log.info(f"Phase 3 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 4: MEDIUM PRIORITY AGENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_4_medium"
        self._log.info("Phase 4: Initializing MEDIUM priority agents...")
        
        # Analytics Agents
        mvp_calc = MVPCalculator(
            config=self._make_config("mvp_calculator", "mvp_calculator", AgentPriority.MEDIUM),
            event_bus=self.event_bus,
        )
        self._register_agent("mvp_calculator", "mvp_calculator", mvp_calc, AgentPriority.MEDIUM)
        
        player_profiler = PlayerProfiler(
            config=self._make_config("player_profiler", "player_profiler", AgentPriority.MEDIUM),
            event_bus=self.event_bus,
        )
        self._register_agent("player_profiler", "player_profiler", player_profiler, AgentPriority.MEDIUM)
        
        team_chemistry = TeamChemistryAnalyzer(
            config=self._make_config("team_chemistry", "team_chemistry", AgentPriority.MEDIUM),
            event_bus=self.event_bus,
        )
        self._register_agent("team_chemistry", "team_chemistry", team_chemistry, AgentPriority.MEDIUM)
        
        perf_tracker = PerformanceTracker(
            config=self._make_config("performance_tracker", "performance_tracker", AgentPriority.MEDIUM),
            event_bus=self.event_bus,
        )
        self._register_agent("performance_tracker", "performance_tracker", perf_tracker, AgentPriority.MEDIUM)
        
        # H33: POVSelector and PlaybookManager are NOT constructed
        # here — they are part of the same deprecated ObserverDirector
        # stack (superseded by services/live_director, "do not wire").

        # Cinematic Controller
        cinematic = CinematicController(
            config=self._make_config("cinematic_controller", "cinematic_controller", AgentPriority.MEDIUM),
            event_bus=self.event_bus,
        )
        self._register_agent("cinematic_controller", "cinematic_controller", cinematic, AgentPriority.MEDIUM)

        self._log.info(f"Phase 4 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 5: LOW PRIORITY AGENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_5_low"
        self._log.info("Phase 5: Initializing LOW priority agents...")
        
        # Prediction Agents
        fight_predictor = FightPredictor(
            config=self._make_config("fight_predictor", "fight_predictor", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("fight_predictor", "fight_predictor", fight_predictor, AgentPriority.LOW)
        
        survival_predictor = SurvivalPredictor(
            config=self._make_config("survival_predictor", "survival_predictor", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("survival_predictor", "survival_predictor", survival_predictor, AgentPriority.LOW)
        
        zone_predictor = ZonePredictor(
            config=self._make_config("zone_predictor", "zone_predictor", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("zone_predictor", "zone_predictor", zone_predictor, AgentPriority.LOW)
        
        winner_predictor = WinnerPredictor(
            config=self._make_config("winner_predictor", "winner_predictor", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("winner_predictor", "winner_predictor", winner_predictor, AgentPriority.LOW)
        
        # Narrative Agents
        storyline_builder = StorylineBuilder(
            config=self._make_config("storyline_builder", "storyline_builder", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("storyline_builder", "storyline_builder", storyline_builder, AgentPriority.LOW)
        
        rivalry_tracker = RivalryTracker(
            config=self._make_config("rivalry_tracker", "rivalry_tracker", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("rivalry_tracker", "rivalry_tracker", rivalry_tracker, AgentPriority.LOW)
        
        comeback_detector = ComebackDetector(
            config=self._make_config("comeback_detector", "comeback_detector", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("comeback_detector", "comeback_detector", comeback_detector, AgentPriority.LOW)
        
        # Highlight Agents
        clip_detector = ClipDetector(
            config=self._make_config("clip_detector", "clip_detector", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("clip_detector", "clip_detector", clip_detector, AgentPriority.LOW)
        
        replay_director = ReplayDirector(
            config=self._make_config("replay_director", "replay_director", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("replay_director", "replay_director", replay_director, AgentPriority.LOW)
        
        montage_generator = MontageGenerator(
            config=self._make_config("montage_generator", "montage_generator", AgentPriority.LOW),
            event_bus=self.event_bus,
        )
        self._register_agent("montage_generator", "montage_generator", montage_generator, AgentPriority.LOW)
        
        self._log.info(f"Phase 5 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # PHASE 6: BACKGROUND AGENTS
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        self.state.startup_phase = "phase_6_background"
        self._log.info("Phase 6: Initializing BACKGROUND agents...")
        
        # Referee Agents
        rule_enforcer = RuleEnforcer(
            config=self._make_config("rule_enforcer", "rule_enforcer", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("rule_enforcer", "rule_enforcer", rule_enforcer, AgentPriority.BACKGROUND)
        
        cheating_detector = CheatingDetector(
            config=self._make_config("cheating_detector", "cheating_detector", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("cheating_detector", "cheating_detector", cheating_detector, AgentPriority.BACKGROUND)
        
        dispute_resolver = DisputeResolver(
            config=self._make_config("dispute_resolver", "dispute_resolver", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("dispute_resolver", "dispute_resolver", dispute_resolver, AgentPriority.BACKGROUND)
        
        # Additional agents
        voice_synth = VoiceSynthesizer(
            config=self._make_config("voice_synthesizer", "voice_synthesizer", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("voice_synthesizer", "voice_synthesizer", voice_synth, AgentPriority.BACKGROUND)
        
        caster_memory = CasterMemory(
            config=self._make_config("caster_memory", "caster_memory", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("caster_memory", "caster_memory", caster_memory, AgentPriority.BACKGROUND)
        
        language_manager = LanguageManager(
            config=self._make_config("language_manager", "language_manager", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("language_manager", "language_manager", language_manager, AgentPriority.BACKGROUND)
        
        scouting_reporter = ScoutingReporter(
            config=self._make_config("scouting_reporter", "scouting_reporter", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("scouting_reporter", "scouting_reporter", scouting_reporter, AgentPriority.BACKGROUND)
        
        documentary_gen = DocumentaryGenerator(
            config=self._make_config("documentary_generator", "documentary_generator", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("documentary_generator", "documentary_generator", documentary_gen, AgentPriority.BACKGROUND)
        
        content_publisher = ContentPublisher(
            config=self._make_config("content_publisher", "content_publisher", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("content_publisher", "content_publisher", content_publisher, AgentPriority.BACKGROUND)
        
        # Memory agents
        team_memory = TeamMemory(
            config=self._make_config("team_memory", "team_memory", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
            team_id="orchestrator", team_name="System",
        )
        self._register_agent("team_memory", "team_memory", team_memory, AgentPriority.BACKGROUND)
        
        engagement_analyzer = EngagementAnalyzer(
            config=self._make_config("engagement_analyzer", "engagement_analyzer", AgentPriority.BACKGROUND),
            event_bus=self.event_bus,
        )
        self._register_agent("engagement_analyzer", "engagement_analyzer", engagement_analyzer, AgentPriority.BACKGROUND)
        
        self._log.info(f"Phase 6 complete: {len(self.agents)} agents")
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # INITIALIZATION COMPLETE
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        
        self.state.total_agents = len(self.agents)
        self.state.startup_phase = "complete"
        self.state.startup_completed = True
        
        self._log.info(
            f"✅ ALL AGENTS INITIALIZED: {self.state.total_agents} agents",
            critical=len(self.registry.get_agents_by_priority(AgentPriority.CRITICAL)),
            high=len(self.registry.get_agents_by_priority(AgentPriority.HIGH)),
            medium=len(self.registry.get_agents_by_priority(AgentPriority.MEDIUM)),
            low=len(self.registry.get_agents_by_priority(AgentPriority.LOW)),
            background=len(self.registry.get_agents_by_priority(AgentPriority.BACKGROUND)),
        )

        # Lifecycle chain event: one agent.registered per initialized agent.
        # Best-effort; the real bus drops (never raises) when Redis is down,
        # and the smoke fake dispatches only to matching subscribers.
        for agent_id, agent in self.agents.items():
            await self._publish_lifecycle(
                "agent.registered",
                {
                    "agent_id": agent_id,
                    "agent_type": agent.config.agent_type,
                    "priority": agent.config.priority.name,
                },
                EventPriority.LOW,
            )

    async def _publish_lifecycle(
        self,
        event_type: str,
        data: Dict[str, Any],
        priority: EventPriority = EventPriority.MEDIUM,
    ) -> None:
        """
        Publish an agent lifecycle event.

        Kwargs (event_type, data, priority, source, match_id) are compatible
        with both the real EventBus.publish and the smoke-test fake. Never
        raises: publish itself never raises on Redis-down, and this is
        additionally guarded.
        """
        try:
            await self.event_bus.publish(
                event_type=event_type,
                data=data,
                priority=priority,
                source="orchestrator",
                match_id=self.state.match_id,
            )
        except Exception as e:
            self._log.warning(
                f"Lifecycle publish failed ({event_type}): {e}"
            )
    
    def _make_config(
        self,
        agent_id: str,
        agent_type: str,
        priority: AgentPriority,
    ) -> AgentConfig:
        """Build an AgentConfig carrying the current match context."""
        return AgentConfig(
            agent_id=agent_id,
            agent_type=agent_type,
            priority=priority,
            match_id=self.state.match_id,
            tournament_id=self.state.tournament_id,
        )
    
    def _register_agent(
        self,
        agent_id: str,
        agent_type: str,
        agent: BaseAgent,
        priority: AgentPriority,
    ) -> None:
        """Register an agent with the orchestrator"""
        self.agents[agent_id] = agent
        self.registry.register(agent_id, agent_type, agent, priority)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LIFECYCLE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start_all_agents(self) -> None:
        """Start all agents in priority order"""
        
        if not self.state.startup_completed:
            self._log.error("Cannot start - initialization not complete")
            return
        
        self.state.is_running = True
        self.state.started_at = time.time()
        
        self._log.info("Starting all agents in priority order...")
        
        priority_order = [
            AgentPriority.CRITICAL,
            AgentPriority.HIGH,
            AgentPriority.MEDIUM,
            AgentPriority.LOW,
            AgentPriority.BACKGROUND,
        ]
        
        for priority in priority_order:
            agents = self.registry.get_agents_by_priority(priority)
            self._log.info(f"Starting {len(agents)} {priority.name} priority agents...")
            
            # Start agents concurrently within same priority
            start_tasks = []
            for agent in agents:
                task = asyncio.create_task(self._start_agent_safely(agent))
                start_tasks.append(task)
            
            results = await asyncio.gather(*start_tasks, return_exceptions=True)
            
            success_count = sum(1 for r in results if r is True)
            fail_count = len(results) - success_count
            
            self._log.info(
                f"{priority.name} agents: {success_count} started, {fail_count} failed"
            )
        
        self.state.active_agents = len(self.registry.get_active_agents())
        
        self._log.info(
            f"✅ ALL AGENTS STARTED: {self.state.active_agents}/{self.state.total_agents} active"
        )
    
    async def _start_agent_safely(self, agent: BaseAgent) -> bool:
        """Start an agent with error handling"""
        agent_id = agent.config.agent_id
        try:
            await agent.start()
            self.registry.mark_active(agent_id)
            await self._publish_lifecycle(
                "agent.started",
                {"agent_id": agent_id, "agent_type": agent.config.agent_type},
                EventPriority.LOW,
            )
            return True
        except Exception as e:
            self._log.error(
                f"Failed to start agent {agent_id}: {e}"
            )
            self.state.startup_errors.append(
                f"{agent_id}: {str(e)}"
            )
            await self._publish_lifecycle(
                "agent.failed",
                {
                    "agent_id": agent_id,
                    "agent_type": agent.config.agent_type,
                    "error": str(e)[:300],
                    "phase": "start",
                },
                EventPriority.HIGH,
            )
            return False
    
    async def stop_all_agents(self) -> None:
        """Stop all agents in reverse priority order"""
        
        self.state.is_running = False
        
        self._log.info("Stopping all agents...")
        
        # Stop in reverse priority (background first, critical last)
        priority_order = [
            AgentPriority.BACKGROUND,
            AgentPriority.LOW,
            AgentPriority.MEDIUM,
            AgentPriority.HIGH,
            AgentPriority.CRITICAL,
        ]
        
        for priority in priority_order:
            agents = self.registry.get_agents_by_priority(priority)
            self._log.info(f"Stopping {len(agents)} {priority.name} priority agents...")
            
            stop_tasks = []
            for agent in agents:
                task = asyncio.create_task(self._stop_agent_safely(agent))
                stop_tasks.append(task)
            
            await asyncio.gather(*stop_tasks, return_exceptions=True)
        
        self.state.active_agents = 0
        
        self._log.info("✅ ALL AGENTS STOPPED")
    
    async def _stop_agent_safely(self, agent: BaseAgent) -> bool:
        """Stop an agent with error handling"""
        agent_id = agent.config.agent_id
        try:
            await agent.stop()
            self.registry.mark_inactive(agent_id)
            await self._publish_lifecycle(
                "agent.stopped",
                {"agent_id": agent_id, "agent_type": agent.config.agent_type},
                EventPriority.LOW,
            )
            return True
        except Exception as e:
            self._log.error(
                f"Failed to stop agent {agent_id}: {e}"
            )
            return False
    
    async def pause_all(self) -> None:
        """Pause all agents"""
        for agent_id, agent in self.agents.items():
            try:
                await agent.pause()
            except Exception as e:
                self._log.error(f"Failed to pause agent {agent_id}: {e}")
        self._log.info("All agents paused")
    
    async def resume_all(self) -> None:
        """Resume all agents"""
        for agent_id, agent in self.agents.items():
            try:
                await agent.resume()
            except Exception as e:
                self._log.error(f"Failed to resume agent {agent_id}: {e}")
        self._log.info("All agents resumed")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MONITORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def monitor_loop(self) -> None:
        """Continuous monitoring loop"""
        while self.state.is_running:
            try:
                self.state.uptime_seconds = time.time() - self.state.started_at
                
                # Check for unhealthy agents
                unhealthy = self.registry.get_unhealthy_agents()
                if unhealthy:
                    self._log.warning(f"Unhealthy agents: {len(unhealthy)}")
                    for agent_id in unhealthy:
                        self._log.warning(f"  - {agent_id}")
                
                # Check for agents needing restart
                needs_restart = self.registry.get_agents_needing_restart()
                if needs_restart:
                    self._log.warning(f"Agents needing restart: {len(needs_restart)}")
                
                # Log summary every 30 seconds
                if int(self.state.uptime_seconds) % 30 == 0:
                    self._log.info(
                        f"System running: {self.state.uptime_seconds:.0f}s",
                        active_agents=self.state.active_agents,
                        unhealthy=len(unhealthy),
                    )
                
                await asyncio.sleep(5)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log.error(f"Monitor error: {e}")
                self.state.runtime_errors += 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Get a specific agent by ID"""
        return self.agents.get(agent_id)
    
    def get_team_agent(self, team_id: str) -> Optional[TeamTracker]:
        """Get team agent for a specific team"""
        return self.team_agents.get(team_id)
    
    def get_summary(self) -> Dict[str, Any]:
        """Get orchestrator summary"""
        return {
            "state": {
                "is_running": self.state.is_running,
                "uptime_seconds": round(self.state.uptime_seconds, 1),
                "match_id": self.state.match_id,
                "startup_phase": self.state.startup_phase,
            },
            "agents": {
                "total": self.state.total_agents,
                "active": self.state.active_agents,
                "by_priority": {
                    p.name: len(self.registry.get_agents_by_priority(p))
                    for p in AgentPriority
                },
            },
            "errors": {
                "startup_errors": len(self.state.startup_errors),
                "runtime_errors": self.state.runtime_errors,
            },
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get orchestrator statistics"""
        return {
            **self.get_summary(),
            "registry": self.registry.get_summary(),
        }
    
    async def shutdown(self) -> None:
        """Complete shutdown of the agent system"""
        self._log.info("Shutting down agent system...")
        await self.stop_all_agents()
        self.registry.reset()
        self.agents.clear()
        self.team_agents.clear()
        self._log.info("Agent system shutdown complete")