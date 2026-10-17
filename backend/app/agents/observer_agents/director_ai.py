# backend/app/agents/observer_agents/director_ai.py
"""
AEGIS AI - Observer Director Agent
Master AI director that controls the entire broadcast.
Makes decisions on which feed to show, when to switch,
when to show replays, and manages broadcast flow.

This is the CRITICAL agent that makes AEGIS feel like
a professional human esports production team.
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

from ..base_agent import BaseAgent, AgentConfig, AgentPriority
from ...core.event_bus import EventBus, EventRecord, EventPriority
from ...config import settings
from .pov_selector import POVSelector, POVCandidate, FeedType
from .cinematic_controller import CinematicController, CameraMovement as CameraMode, TransitionType
from .safety_controller import SafetyController
from .playbook_manager import PlaybookManager

logger = structlog.get_logger("aegis.agents.observer_director")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BroadcastState(str, Enum):
    """Current broadcast state"""
    IDLE = "idle"
    MATCH_START = "match_start"
    EARLY_GAME = "early_game"
    MID_GAME = "mid_game"
    LATE_GAME = "late_game"
    FINAL_CIRCLE = "final_circle"
    MATCH_END = "match_end"
    REPLAY = "replay"
    HIGHLIGHT = "highlight"

class DirectorDecision(str, Enum):
    """Types of decisions the director can make"""
    SWITCH_FEED = "switch_feed"
    STAY_ON_CURRENT = "stay_on_current"
    SHOW_REPLAY = "show_replay"
    SHOW_OVERVIEW = "show_overview"
    SHOW_SCOREBOARD = "show_scoreboard"
    FOLLOW_PLAYER = "follow_player"
    FOLLOW_TEAM = "follow_team"
    CINEMATIC_SHOT = "cinematic_shot"

@dataclass
class BroadcastScene:
    """Current broadcast scene"""
    feed_type: FeedType = FeedType.TEAM_POV
    team_id: Optional[str] = None
    player_id: Optional[str] = None
    fight_id: Optional[str] = None
    camera_mode: CameraMode = CameraMode.TRACKING
    transition: TransitionType = TransitionType.CUT
    started_at: float = field(default_factory=time.time)
    duration: float = 0.0
    reason: str = ""

@dataclass
class DirectorState:
    """Director's current state and decision context"""
    broadcast_state: BroadcastState = BroadcastState.IDLE
    current_scene: Optional[BroadcastScene] = None
    previous_scenes: deque = field(default_factory=lambda: deque(maxlen=20))
    
    # Match context
    match_id: Optional[str] = None
    match_time_seconds: float = 0.0
    zone_phase: int = 0
    teams_alive: int = 16
    players_alive: int = 64
    
    # Active tracking
    active_fights: Dict[str, Any] = field(default_factory=dict)
    team_rankings: Dict[str, int] = field(default_factory=dict)
    
    # Decision history
    recent_decisions: deque = field(default_factory=lambda: deque(maxlen=50))
    switch_count_last_minute: int = 0
    last_switch_time: float = 0.0
    
    # Priority tracking
    current_priority_score: float = 0.0
    candidate_priority_score: float = 0.0
    
    # Replay buffer
    replay_buffer: deque = field(default_factory=lambda: deque(maxlen=10))
    last_replay_time: float = 0.0
    
    # Statistics
    total_scene_changes: int = 0
    total_replays_shown: int = 0
    avg_scene_duration: float = 0.0


class ObserverDirector(BaseAgent):
    """
    Master AI Director - The brain of broadcast production.
    
    Responsibilities:
    1. Decide which feed to show at any moment
    2. Manage broadcast flow (early game → mid game → late game)
    3. Prioritize fights and clutch moments
    4. Control camera transitions and cinematic effects
    5. Schedule replays at appropriate times
    6. Maintain human-like observing patterns
    7. Coordinate with safety controller to prevent chaos
    8. Follow production playbook for consistent quality
    
    This agent makes AEGIS feel like a professional human
    esports production team, not an AI automation system.
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        
        # Director needs a responsive but not frantic tick rate
        self.config.tick_interval_ms = 200  # 5Hz
        
        # Sub-modules (owned components, not independently lifecycle-managed)
        def _sub_config(name: str) -> AgentConfig:
            return AgentConfig(
                agent_id=f"{config.agent_id}_{name}",
                agent_type=name,
                priority=config.priority,
                match_id=config.match_id,
                tournament_id=config.tournament_id,
            )
        
        self.pov_selector = POVSelector(
            config=_sub_config("pov_selector"), event_bus=event_bus
        )
        self.cinematic = CinematicController(
            config=_sub_config("cinematic_controller"), event_bus=event_bus
        )
        self.safety = SafetyController(
            config=_sub_config("safety_controller"), event_bus=event_bus
        )
        self.playbook = PlaybookManager(
            config=_sub_config("playbook_manager"), event_bus=event_bus
        )
        
        # Director state
        self.director_state = DirectorState()
        
        # Configuration
        self.min_scene_duration = settings.MIN_SCENE_TIME_SECONDS
        self.max_switches_per_minute = settings.MAX_SWITCHES_PER_MINUTE
        self.replay_cooldown = 15  # seconds between replays
        self.clutch_lock_duration = 8  # seconds to stay on clutch
        self.fight_lock_duration = 6  # seconds minimum on fight
        
        # Team priority (can be set by tournament)
        self.priority_teams: Set[str] = set()
        self.priority_players: Set[str] = set()
        
        self._log = logger.bind(agent="observer_director")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def setup(self) -> None:
        """Setup observer director"""
        # Subscribe to critical events
        self.subscribe_to_event("fight_detected")
        self.subscribe_to_event("fight_ended")
        self.subscribe_to_event("clutch_detected")
        self.subscribe_to_event("clutch_resolved")
        self.subscribe_to_event("team_eliminated")
        self.subscribe_to_event("player_knocked")
        self.subscribe_to_event("kill_confirmed")
        self.subscribe_to_event("zone_change")
        self.subscribe_to_event("match_state_change")
        self.subscribe_to_event("highlight_moment")
        
        # Listen for fight coordinator messages
        self.event_bus.subscribe(
            subscriber_id="observer_fight_updates",
            callback=self._on_fight_update,
            event_types=["important_fight", "fight_update", "new_fight"],
        )
        
        self._log.info("Observer Director setup complete")
    
    async def cleanup(self) -> None:
        """Cleanup"""
        self._log.info("Observer Director cleanup")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MAIN TICK - DECISION LOOP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def tick(self) -> None:
        """Main decision loop - runs every 200ms"""
        
        # Update match context
        self._update_match_context()
        
        # Update broadcast state based on match phase
        self._update_broadcast_state()
        
        # Check safety constraints
        if not self.safety.can_switch(self.director_state):
            return  # Safety prevents switching
        
        # Get current scene duration
        if self.director_state.current_scene:
            self.director_state.current_scene.duration = (
                time.time() - self.director_state.current_scene.started_at
            )
        
        # Run playbook for current state
        playbook_action = self.playbook.get_action(
            self.director_state.broadcast_state,
            self.director_state,
        )
        
        if playbook_action:
            await self._execute_playbook_action(playbook_action)
            return
        
        # Main decision logic
        decision = await self._make_director_decision()
        
        if decision:
            await self._execute_decision(decision)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # EVENT HANDLING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def handle_event(self, record: EventRecord) -> None:
        """Handle incoming events"""
        event = record.event
        event_type = event.event_type
        data = event.data
        
        if event_type == "fight_detected":
            await self._on_fight_detected(data)
        elif event_type == "fight_ended":
            await self._on_fight_ended(data)
        elif event_type == "clutch_detected":
            await self._on_clutch_detected(data)
        elif event_type == "clutch_resolved":
            await self._on_clutch_resolved(data)
        elif event_type == "team_eliminated":
            await self._on_team_eliminated(data)
        elif event_type == "kill_confirmed":
            await self._on_kill(data)
        elif event_type == "player_knocked":
            await self._on_knock(data)
        elif event_type == "zone_change":
            await self._on_zone_change(data)
        elif event_type == "highlight_moment":
            await self._on_highlight_moment(data)
    
    async def _on_fight_detected(self, data: Dict[str, Any]) -> None:
        """Handle new fight detection"""
        fight_id = data.get("fight_id")
        teams = data.get("teams", [])
        intensity = data.get("intensity", "medium")
        intensity_score = data.get("intensity_score", 50)
        is_clutch = data.get("is_clutch", False)
        entertainment = data.get("entertainment_score", 50)
        
        self.director_state.active_fights[fight_id] = {
            "teams": teams,
            "intensity": intensity,
            "intensity_score": intensity_score,
            "is_clutch": is_clutch,
            "entertainment_score": entertainment,
            "detected_at": time.time(),
        }
        
        # If this is a high-priority fight, consider switching immediately
        if intensity_score > 70 or is_clutch:
            candidate = POVCandidate(
                feed_type=FeedType.FIGHT,
                fight_id=fight_id,
                priority_score=intensity_score,
                reason=f"High intensity fight ({intensity})",
                is_clutch=is_clutch,
            )
            
            self.director_state.candidate_priority_score = intensity_score
            
            # Force switch if very important
            if intensity_score > 85 or is_clutch:
                await self._switch_to_fight(fight_id, "Critical fight detected")
    
    async def _on_fight_ended(self, data: Dict[str, Any]) -> None:
        """Handle fight ending"""
        fight_id = data.get("fight_id")
        
        if fight_id in self.director_state.active_fights:
            del self.director_state.active_fights[fight_id]
        
        # If currently showing this fight, consider switching away
        if (self.director_state.current_scene and 
            self.director_state.current_scene.fight_id == fight_id):
            # Don't switch immediately - let the scene play out naturally
            self.director_state.current_scene.reason = "Fight ended - finding next scene"
    
    async def _on_clutch_detected(self, data: Dict[str, Any]) -> None:
        """Handle clutch situation detection - HIGHEST PRIORITY"""
        clutch_id = data.get("clutch_id")
        player_name = data.get("player_name", "Unknown")
        probability = data.get("probability", 0.2)
        
        self._log.warning(
            f"🎯 CLUTCH ALERT: {player_name} | "
            f"Win probability: {probability:.1%} | "
            f"Switching immediately!"
        )
        
        # Force switch to clutch - this is the highest priority
        await self._switch_to_clutch(clutch_id, f"Clutch: {player_name}")
    
    async def _on_clutch_resolved(self, data: Dict[str, Any]) -> None:
        """Handle clutch resolution"""
        won = data.get("won", False)
        player_name = data.get("player_name", "Unknown")
        
        if won:
            # Queue replay of the clutch
            self.director_state.replay_buffer.append({
                "type": "clutch",
                "data": data,
                "priority": 100,
                "timestamp": time.time(),
            })
            
            self._log.warning(f"🏆 Clutch won by {player_name} - queuing replay")
    
    async def _on_kill(self, data: Dict[str, Any]) -> None:
        """Handle kill event"""
        # Check if this kill is part of an active fight we're watching
        fight_id = data.get("fight_id")
        
        if fight_id and self.director_state.current_scene:
            if self.director_state.current_scene.fight_id == fight_id:
                # We're already watching this fight - update POV to killer
                killer_id = data.get("killer_id")
                if killer_id:
                    await self._follow_player(killer_id, "Kill follow")
    
    async def _on_knock(self, data: Dict[str, Any]) -> None:
        """Handle knock event"""
        fight_id = data.get("fight_id")
        
        # If in a fight, a knock is significant
        if fight_id and fight_id in self.director_state.active_fights:
            # Could switch to show the knock
            pass
    
    async def _on_zone_change(self, data: Dict[str, Any]) -> None:
        """Handle zone change"""
        self.director_state.zone_phase = data.get("phase", self.director_state.zone_phase)
        
        # Final zones require different broadcast approach
        if self.director_state.zone_phase >= 6:
            self.director_state.broadcast_state = BroadcastState.FINAL_CIRCLE
    
    async def _on_highlight_moment(self, data: Dict[str, Any]) -> None:
        """Handle auto-detected highlight moment"""
        # Queue for replay consideration
        self.director_state.replay_buffer.append({
            "type": "highlight",
            "data": data,
            "priority": data.get("priority", 50),
            "timestamp": time.time(),
        })
    
    async def _on_fight_update(self, record: EventRecord) -> None:
        """Handle fight coordinator updates"""
        data = record.event.data
        fight_id = data.get("fight_id")
        
        if fight_id and fight_id in self.director_state.active_fights:
            self.director_state.active_fights[fight_id].update(data)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DIRECTOR DECISION MAKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _make_director_decision(self) -> Optional[Dict[str, Any]]:
        """Make the main director decision - what to show next"""
        
        # Don't change if minimum scene time not met
        if (self.director_state.current_scene and 
            self.director_state.current_scene.duration < self.min_scene_duration):
            return None
        
        # Get all possible candidates for what to show
        candidates = self.pov_selector.get_candidates(
            self.director_state,
            self.director_state.active_fights,
        )
        
        if not candidates:
            return None
        
        # Filter through safety controller
        safe_candidates = self.safety.filter_candidates(
            candidates,
            self.director_state,
        )
        
        if not safe_candidates:
            return None
        
        # Select best candidate
        best_candidate = self.pov_selector.select_best_candidate(
            safe_candidates,
            self.director_state,
        )
        
        if not best_candidate:
            return None
        
        # Compare with current scene
        if self.director_state.current_scene:
            # Calculate current scene's score
            current_score = self._calculate_current_scene_score()
            
            # Only switch if significantly better
            score_difference = best_candidate.priority_score - current_score
            
            # Minimum difference threshold to prevent flip-flopping
            min_difference = 15
            
            # Lower threshold for clutch situations
            if best_candidate.is_clutch:
                min_difference = 5
            
            # Lower threshold if current scene is stale
            if self.director_state.current_scene.duration > 20:
                min_difference = 10
            
            if score_difference < min_difference:
                return None  # Stay on current scene
        
        # Build decision
        decision = {
            "type": DirectorDecision.SWITCH_FEED,
            "candidate": best_candidate,
            "reason": best_candidate.reason,
        }
        
        return decision
    
    def _calculate_current_scene_score(self) -> float:
        """Calculate priority score for current scene"""
        if not self.director_state.current_scene:
            return 0.0
        
        score = 50.0
        
        # Active fight bonus
        if self.director_state.current_scene.fight_id:
            fight = self.director_state.active_fights.get(self.director_state.current_scene.fight_id, {})
            score = fight.get("intensity_score", score)
            
            # Clutch bonus
            if fight.get("is_clutch"):
                score += 25
        
        # Priority team/player bonus
        if self.director_state.current_scene.team_id in self.priority_teams:
            score += 15
        if self.director_state.current_scene.player_id in self.priority_players:
            score += 15
        
        # Recent scene penalty (prevent staying too long)
        duration = self.director_state.current_scene.duration
        if duration > 30:
            score -= (duration - 30) * 0.5  # Gradually decrease after 30 seconds
        
        return max(0, score)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DECISION EXECUTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _execute_decision(self, decision: Dict[str, Any]) -> None:
        """Execute a director decision"""
        
        decision_type = decision.get("type")
        candidate = decision.get("candidate")
        reason = decision.get("reason", "")
        
        if decision_type == DirectorDecision.SWITCH_FEED:
            await self._switch_feed(candidate, reason)
        elif decision_type == DirectorDecision.SHOW_REPLAY:
            await self._show_replay(decision.get("replay_data"))
        elif decision_type == DirectorDecision.FOLLOW_PLAYER:
            await self._follow_player(
                decision.get("player_id"),
                reason,
            )
        elif decision_type == DirectorDecision.CINEMATIC_SHOT:
            await self._cinematic_shot(decision.get("mode"), reason)
    
    async def _switch_feed(self, candidate: POVCandidate, reason: str) -> None:
        """Switch to a new feed"""
        
        # Save current scene to history
        if self.director_state.current_scene:
            self.director_state.previous_scenes.append(self.director_state.current_scene)
        
        # Determine transition type
        transition = self.cinematic.get_transition(
            self.director_state.current_scene,
            candidate,
        )
        
        # Create new scene
        new_scene = BroadcastScene(
            feed_type=candidate.feed_type,
            team_id=candidate.team_id,
            player_id=candidate.player_id,
            fight_id=candidate.fight_id,
            camera_mode=candidate.camera_mode,
            transition=transition,
            reason=reason,
        )
        
        # Safety check
        if not self.safety.validate_switch(self.director_state.current_scene, new_scene):
            self._log.warning(f"Safety prevented switch: {reason}")
            return
        
        # Execute the switch
        self.director_state.current_scene = new_scene
        self.director_state.last_switch_time = time.time()
        self.director_state.switch_count_last_minute += 1
        self.director_state.total_scene_changes += 1
        
        # Record decision
        self.director_state.recent_decisions.append({
            "timestamp": time.time(),
            "decision": "switch_feed",
            "to": candidate.feed_type.value,
            "reason": reason,
        })
        
        # Apply cinematic transition
        await self.cinematic.apply_transition(transition)
        
        # Send OBS command
        await self._send_obs_switch_command(new_scene)
        
        self._log.info(
            f"📺 SWITCH: {candidate.feed_type.value} | "
            f"Team: {candidate.team_id or 'N/A'} | "
            f"Player: {candidate.player_id or 'N/A'} | "
            f"Reason: {reason}"
        )
    
    async def _switch_to_fight(self, fight_id: str, reason: str) -> None:
        """Switch to a specific fight"""
        fight = self.director_state.active_fights.get(fight_id, {})
        teams = fight.get("teams", [])
        
        # Select best team POV for this fight
        best_team = self.pov_selector.select_best_team_for_fight(
            fight_id,
            teams,
            self.director_state,
        )
        
        candidate = POVCandidate(
            feed_type=FeedType.FIGHT,
            fight_id=fight_id,
            team_id=best_team,
            priority_score=85,
            reason=reason,
            is_clutch=fight.get("is_clutch", False),
        )
        
        await self._switch_feed(candidate, reason)
    
    async def _switch_to_clutch(self, clutch_id: str, reason: str) -> None:
        """Switch to a clutch situation - highest priority"""
        candidate = POVCandidate(
            feed_type=FeedType.PLAYER_POV,
            player_id=clutch_id,
            priority_score=100,
            reason=reason,
            is_clutch=True,
            camera_mode=CameraMode.TRACKING,
        )
        
        await self._switch_feed(candidate, reason)
    
    async def _follow_player(self, player_id: str, reason: str) -> None:
        """Follow a specific player"""
        candidate = POVCandidate(
            feed_type=FeedType.PLAYER_POV,
            player_id=player_id,
            priority_score=70,
            reason=reason,
        )
        
        await self._switch_feed(candidate, reason)
    
    async def _show_replay(self, replay_data: Dict[str, Any]) -> None:
        """Show a replay"""
        # Check replay cooldown
        if time.time() - self.director_state.last_replay_time < self.replay_cooldown:
            return
        
        # Save current scene for return
        return_scene = self.director_state.current_scene
        
        # Switch to replay
        self.director_state.broadcast_state = BroadcastState.REPLAY
        self.director_state.last_replay_time = time.time()
        self.director_state.total_replays_shown += 1
        
        # Send OBS replay command
        await self.event_bus.publish(
            event_type="broadcast_command",
            data={
                "command": "show_replay",
                "replay_data": replay_data,
                "return_scene": {
                    "feed_type": return_scene.feed_type.value if return_scene else None,
                    "team_id": return_scene.team_id if return_scene else None,
                    "player_id": return_scene.player_id if return_scene else None,
                },
            },
            priority=EventPriority.HIGH,
            source=self.config.agent_id,
        )
        
        self._log.info(f"🔄 REPLAY shown: {replay_data.get('type', 'unknown')}")
    
    async def _cinematic_shot(self, mode: str, reason: str) -> None:
        """Switch to a cinematic camera shot"""
        candidate = POVCandidate(
            feed_type=FeedType.CINEMATIC,
            camera_mode=CameraMode(mode) if mode else CameraMode.ORBIT,
            priority_score=60,
            reason=reason,
        )
        
        await self._switch_feed(candidate, reason)
    
    async def _execute_playbook_action(self, action: Dict[str, Any]) -> None:
        """Execute a playbook-prescribed action"""
        action_type = action.get("type")
        
        if action_type == "switch_feed":
            await self._switch_feed(
                action.get("candidate"),
                action.get("reason", "Playbook action"),
            )
        elif action_type == "show_replay":
            await self._show_replay(action.get("replay_data"))
        elif action_type == "cinematic":
            await self._cinematic_shot(
                action.get("mode"),
                action.get("reason", "Playbook cinematic"),
            )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OBS COMMUNICATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _send_obs_switch_command(self, scene: BroadcastScene) -> None:
        """Send scene switch command to OBS"""
        
        command = {
            "command": "switch_scene",
            "scene_type": scene.feed_type.value,
            "team_id": scene.team_id,
            "player_id": scene.player_id,
            "camera_mode": scene.camera_mode.value,
            "transition": scene.transition.value,
            "timestamp": time.time(),
        }
        
        await self.event_bus.publish(
            event_type="broadcast_command",
            data=command,
            priority=EventPriority.CRITICAL,
            source=self.config.agent_id,
            match_id=self.director_state.match_id,
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _update_match_context(self) -> None:
        """Update match context from event bus state"""
        # Update switch count per minute
        recent_switches = [
            d for d in self.director_state.recent_decisions
            if time.time() - d["timestamp"] < 60
            and d.get("decision") == "switch_feed"
        ]
        self.director_state.switch_count_last_minute = len(recent_switches)
    
    def _update_broadcast_state(self) -> None:
        """Update broadcast state based on match phase"""
        
        # Match start
        if self.director_state.teams_alive == 16 and self.director_state.match_time_seconds < 60:
            self.director_state.broadcast_state = BroadcastState.EARLY_GAME
        
        # Mid game
        elif self.director_state.teams_alive > 5 and self.director_state.zone_phase < 6:
            self.director_state.broadcast_state = BroadcastState.MID_GAME
        
        # Late game
        elif self.director_state.teams_alive <= 5 and self.director_state.zone_phase >= 5:
            self.director_state.broadcast_state = BroadcastState.LATE_GAME
        
        # Final circle
        elif self.director_state.zone_phase >= 7 and self.director_state.teams_alive <= 3:
            self.director_state.broadcast_state = BroadcastState.FINAL_CIRCLE
        
        # Match end
        elif self.director_state.teams_alive <= 1:
            self.director_state.broadcast_state = BroadcastState.MATCH_END
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES & CONTROL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_priority_teams(self, team_ids: List[str]) -> None:
        """Set priority teams for broadcast focus"""
        self.priority_teams = set(team_ids)
        self._log.info(f"Priority teams set: {self.priority_teams}")
    
    def set_priority_players(self, player_ids: List[str]) -> None:
        """Set priority players for broadcast focus"""
        self.priority_players = set(player_ids)
        self._log.info(f"Priority players set: {self.priority_players}")
    
    def force_switch(self, target: Dict[str, Any]) -> None:
        """Force a manual switch (human override)"""
        candidate = POVCandidate(
            feed_type=FeedType(target.get("feed_type", "team_pov")),
            team_id=target.get("team_id"),
            player_id=target.get("player_id"),
            priority_score=100,
            reason="Manual override",
        )
        
        asyncio.create_task(self._switch_feed(candidate, "Manual override"))
    
    def get_current_scene(self) -> Optional[Dict[str, Any]]:
        """Get current broadcast scene info"""
        if not self.director_state.current_scene:
            return None
        
        return {
            "feed_type": self.director_state.current_scene.feed_type.value,
            "team_id": self.director_state.current_scene.team_id,
            "player_id": self.director_state.current_scene.player_id,
            "fight_id": self.director_state.current_scene.fight_id,
            "camera_mode": self.director_state.current_scene.camera_mode.value,
            "duration": round(self.director_state.current_scene.duration, 1),
            "reason": self.director_state.current_scene.reason,
        }
    
    def get_director_summary(self) -> Dict[str, Any]:
        """Get director state summary"""
        return {
            "broadcast_state": self.director_state.broadcast_state.value,
            "current_scene": self.get_current_scene(),
            "active_fights": len(self.director_state.active_fights),
            "teams_alive": self.director_state.teams_alive,
            "zone_phase": self.director_state.zone_phase,
            "total_scene_changes": self.director_state.total_scene_changes,
            "total_replays": self.director_state.total_replays_shown,
            "switches_last_minute": self.director_state.switch_count_last_minute,
            "replay_buffer_size": len(self.director_state.replay_buffer),
        }
    
    def reset_for_match(self, match_id: str) -> None:
        """Reset for new match"""
        self.director_state = DirectorState(match_id=match_id)
        self.pov_selector.reset()
        self.safety.reset()
        self._log.info(f"Director reset for match: {match_id}")