# backend/app/agents/observer_agents/playbook_manager.py
"""
AEGIS AI - Playbook Manager
Production playbooks that define broadcast behavior
for different match situations. Makes AI broadcasts feel
like professional human productions.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.playbook")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class PlaybookType(str, Enum):
    """Types of production playbooks"""
    EARLY_GAME = "early_game"
    MID_GAME = "mid_game"
    LATE_GAME = "late_game"
    FINAL_CIRCLE = "final_circle"
    MATCH_START = "match_start"
    MATCH_END = "match_end"
    REPLAY = "replay"
    CLUTCH = "clutch"
    RIVALRY = "rivalry"
    DEFAULT = "default"

class PlaybookAction(str, Enum):
    """Actions defined in playbooks"""
    SWITCH_TO_FIGHT = "switch_to_fight"
    SWITCH_TO_TEAM = "switch_to_team"
    SWITCH_TO_PLAYER = "switch_to_player"
    SHOW_OVERVIEW = "show_overview"
    SHOW_REPLAY = "show_replay"
    CINEMATIC_SHOT = "cinematic_shot"
    FOLLOW_ACTION = "follow_action"
    HOLD_CURRENT = "hold_current"
    ROTATE_TEAMS = "rotate_teams"
    SHOW_SCOREBOARD = "show_scoreboard"

@dataclass
class PlaybookRule:
    """A single rule in a playbook"""
    rule_id: str
    description: str
    condition: str  # Python expression to evaluate
    action: PlaybookAction
    priority: int = 50
    cooldown: float = 0.0  # Seconds before can trigger again
    last_triggered: float = 0.0

@dataclass
class Playbook:
    """A complete production playbook"""
    playbook_type: PlaybookType
    name: str
    description: str
    rules: List[PlaybookRule] = field(default_factory=list)
    is_active: bool = True
    priority: int = 50


class PlaybookManager:
    """
    Manages production playbooks for different match situations.
    
    Playbooks define how the broadcast should behave in different
    game phases to create professional-quality productions.
    
    Each playbook contains rules that trigger based on
    match conditions and direct the Observer Director.
    """
    
    def __init__(self):
        # All playbooks
        self.playbooks: Dict[PlaybookType, Playbook] = {}
        
        # Active playbook
        self.active_playbook: Optional[PlaybookType] = None
        
        # Playbook history
        self.playbook_history: deque = deque(maxlen=20)
        
        # Action cooldowns
        self.action_cooldowns: Dict[str, float] = {}
        
        # Initialize default playbooks
        self._initialize_playbooks()
        
        self._log = logger.bind(component="playbook")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBOOK INITIALIZATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _initialize_playbooks(self) -> None:
        """Initialize all production playbooks"""
        
        # Early Game Playbook
        self.playbooks[PlaybookType.EARLY_GAME] = Playbook(
            playbook_type=PlaybookType.EARLY_GAME,
            name="Early Game Coverage",
            description="Landing phase and early rotations. Show landings, early fights, and looting.",
            priority=30,
            rules=[
                PlaybookRule(
                    rule_id="early_fight",
                    description="Switch to any early game fight",
                    condition="has_active_fight and fight_intensity > 40",
                    action=PlaybookAction.SWITCH_TO_FIGHT,
                    priority=70,
                    cooldown=8.0,
                ),
                PlaybookRule(
                    rule_id="hot_drop",
                    description="Show contested landing zones",
                    condition="match_time < 120 and has_contested_landing",
                    action=PlaybookAction.SWITCH_TO_TEAM,
                    priority=60,
                    cooldown=10.0,
                ),
                PlaybookRule(
                    rule_id="rotate_teams_early",
                    description="Rotate through teams showing positions",
                    condition="match_time > 60 and not has_active_fight",
                    action=PlaybookAction.ROTATE_TEAMS,
                    priority=40,
                    cooldown=15.0,
                ),
            ],
        )
        
        # Mid Game Playbook
        self.playbooks[PlaybookType.MID_GAME] = Playbook(
            playbook_type=PlaybookType.MID_GAME,
            name="Mid Game Coverage",
            description="Rotations, mid-game fights, zone positioning.",
            priority=50,
            rules=[
                PlaybookRule(
                    rule_id="mid_fight",
                    description="Switch to active fights",
                    condition="has_active_fight",
                    action=PlaybookAction.SWITCH_TO_FIGHT,
                    priority=80,
                    cooldown=6.0,
                ),
                PlaybookRule(
                    rule_id="follow_star",
                    description="Follow star player if no fights",
                    condition="not has_active_fight and has_star_player_alive",
                    action=PlaybookAction.SWITCH_TO_PLAYER,
                    priority=50,
                    cooldown=12.0,
                ),
                PlaybookRule(
                    rule_id="zone_position",
                    description="Show teams near zone edge",
                    condition="not has_active_fight and zone_changed",
                    action=PlaybookAction.SHOW_OVERVIEW,
                    priority=45,
                    cooldown=20.0,
                ),
            ],
        )
        
        # Late Game Playbook
        self.playbooks[PlaybookType.LATE_GAME] = Playbook(
            playbook_type=PlaybookType.LATE_GAME,
            name="Late Game Coverage",
            description="Fewer teams, higher stakes. Focus on positioning and fights.",
            priority=70,
            rules=[
                PlaybookRule(
                    rule_id="late_fight",
                    description="Any fight is critical",
                    condition="has_active_fight",
                    action=PlaybookAction.SWITCH_TO_FIGHT,
                    priority=90,
                    cooldown=5.0,
                ),
                PlaybookRule(
                    rule_id="show_survivors",
                    description="Show surviving team positions",
                    condition="not has_active_fight",
                    action=PlaybookAction.ROTATE_TEAMS,
                    priority=60,
                    cooldown=8.0,
                ),
            ],
        )
        
        # Final Circle Playbook
        self.playbooks[PlaybookType.FINAL_CIRCLE] = Playbook(
            playbook_type=PlaybookType.FINAL_CIRCLE,
            name="Final Circle Coverage",
            description="Maximum tension. Stable coverage, fewer switches, dramatic shots.",
            priority=90,
            rules=[
                PlaybookRule(
                    rule_id="final_fight",
                    description="Show final fights - DO NOT SWITCH AWAY",
                    condition="has_active_fight",
                    action=PlaybookAction.HOLD_CURRENT,
                    priority=100,
                    cooldown=0.0,
                ),
                PlaybookRule(
                    rule_id="final_overview",
                    description="Show final circle overview",
                    condition="teams_alive <= 3 and not has_active_fight",
                    action=PlaybookAction.CINEMATIC_SHOT,
                    priority=85,
                    cooldown=10.0,
                ),
                PlaybookRule(
                    rule_id="final_positioning",
                    description="Show team positions in final circle",
                    condition="teams_alive <= 5",
                    action=PlaybookAction.SHOW_OVERVIEW,
                    priority=70,
                    cooldown=12.0,
                ),
            ],
        )
        
        # Clutch Playbook
        self.playbooks[PlaybookType.CLUTCH] = Playbook(
            playbook_type=PlaybookType.CLUTCH,
            name="Clutch Coverage",
            description="Solo/duo clutch situations. Maximum focus, no switching.",
            priority=100,
            rules=[
                PlaybookRule(
                    rule_id="clutch_focus",
                    description="LOCK on clutch - highest priority",
                    condition="is_clutch",
                    action=PlaybookAction.HOLD_CURRENT,
                    priority=100,
                    cooldown=0.0,
                ),
                PlaybookRule(
                    rule_id="clutch_player",
                    description="Follow clutch player",
                    condition="is_clutch",
                    action=PlaybookAction.FOLLOW_ACTION,
                    priority=100,
                    cooldown=0.0,
                ),
            ],
        )
        
        # Replay Playbook
        self.playbooks[PlaybookType.REPLAY] = Playbook(
            playbook_type=PlaybookType.REPLAY,
            name="Replay Coverage",
            description="Show replays of important moments.",
            priority=60,
            rules=[
                PlaybookRule(
                    rule_id="show_replay",
                    description="Show queued replay",
                    condition="has_replay_queued and not is_clutch",
                    action=PlaybookAction.SHOW_REPLAY,
                    priority=75,
                    cooldown=15.0,
                ),
            ],
        )
        
        # Match Start Playbook
        self.playbooks[PlaybookType.MATCH_START] = Playbook(
            playbook_type=PlaybookType.MATCH_START,
            name="Match Start",
            description="Pre-match buildup, team introductions, plane path.",
            priority=40,
            rules=[
                PlaybookRule(
                    rule_id="plane_path",
                    description="Show plane path and drop",
                    condition="match_time < 30",
                    action=PlaybookAction.CINEMATIC_SHOT,
                    priority=70,
                    cooldown=30.0,
                ),
                PlaybookRule(
                    rule_id="team_intro",
                    description="Show priority teams",
                    condition="match_time < 60",
                    action=PlaybookAction.ROTATE_TEAMS,
                    priority=50,
                    cooldown=10.0,
                ),
            ],
        )
        
        # Match End Playbook
        self.playbooks[PlaybookType.MATCH_END] = Playbook(
            playbook_type=PlaybookType.MATCH_END,
            name="Match End",
            description="Winner celebration, final stats, match summary.",
            priority=80,
            rules=[
                PlaybookRule(
                    rule_id="winner_focus",
                    description="Show winning team celebration",
                    condition="match_ended",
                    action=PlaybookAction.SWITCH_TO_TEAM,
                    priority=90,
                    cooldown=0.0,
                ),
                PlaybookRule(
                    rule_id="final_scoreboard",
                    description="Show final scoreboard",
                    condition="match_ended and scene_duration > 5",
                    action=PlaybookAction.SHOW_SCOREBOARD,
                    priority=80,
                    cooldown=0.0,
                ),
            ],
        )
        
        self._log.info(f"Initialized {len(self.playbooks)} playbooks")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBOOK SELECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_active_playbook(self, broadcast_state: str) -> Optional[Playbook]:
        """Get the playbook for current broadcast state"""
        
        # Map broadcast state to playbook type
        state_to_playbook = {
            "early_game": PlaybookType.EARLY_GAME,
            "mid_game": PlaybookType.MID_GAME,
            "late_game": PlaybookType.LATE_GAME,
            "final_circle": PlaybookType.FINAL_CIRCLE,
            "match_start": PlaybookType.MATCH_START,
            "match_end": PlaybookType.MATCH_END,
        }
        
        playbook_type = state_to_playbook.get(broadcast_state)
        
        if playbook_type:
            playbook = self.playbooks.get(playbook_type)
            if playbook and playbook.is_active:
                # Record playbook change
                if self.active_playbook != playbook_type:
                    self.playbook_history.append({
                        "from": self.active_playbook,
                        "to": playbook_type,
                        "timestamp": time.time(),
                    })
                    self.active_playbook = playbook_type
                    self._log.info(f"Playbook changed: {playbook_type.value}")
                
                return playbook
        
        return None
    
    def get_action(
        self,
        broadcast_state: str,
        director_state: Any,
    ) -> Optional[Dict[str, Any]]:
        """Get the next action from the active playbook"""
        
        # Get active playbook
        playbook = self.get_active_playbook(broadcast_state)
        
        # Also check clutch playbook (highest priority)
        clutch_playbook = self.playbooks.get(PlaybookType.CLUTCH)
        
        # Check replay playbook
        replay_playbook = self.playbooks.get(PlaybookType.REPLAY)
        
        # Build evaluation context
        context = self._build_context(director_state)
        
        # Evaluate rules from all applicable playbooks
        all_rules = []
        
        # Clutch rules always take precedence
        if clutch_playbook and clutch_playbook.is_active:
            for rule in clutch_playbook.rules:
                if self._evaluate_condition(rule.condition, context):
                    all_rules.append((rule, 100))  # Clutch priority
        
        # Current playbook rules
        if playbook:
            for rule in playbook.rules:
                if self._evaluate_condition(rule.condition, context):
                    all_rules.append((rule, playbook.priority))
        
        # Replay rules
        if replay_playbook and replay_playbook.is_active:
            for rule in replay_playbook.rules:
                if self._evaluate_condition(rule.condition, context):
                    all_rules.append((rule, 60))
        
        if not all_rules:
            return None
        
        # Sort by priority (playbook priority + rule priority)
        all_rules.sort(key=lambda x: x[1] + x[0].priority, reverse=True)
        
        # Get best rule
        best_rule, _ = all_rules[0]
        
        # Check cooldown
        if best_rule.cooldown > 0:
            last_triggered = self.action_cooldowns.get(best_rule.rule_id, 0)
            if time.time() - last_triggered < best_rule.cooldown:
                return None
        
        # Update cooldown
        self.action_cooldowns[best_rule.rule_id] = time.time()
        best_rule.last_triggered = time.time()
        
        # Build action
        action = {
            "type": best_rule.action.value,
            "rule_id": best_rule.rule_id,
            "reason": best_rule.description,
            "priority": best_rule.priority,
        }
        
        self._log.debug(f"Playbook action: {best_rule.action.value} - {best_rule.description}")
        
        return action
    
    def _build_context(self, director_state: Any) -> Dict[str, Any]:
        """Build evaluation context from director state"""
        return {
            "has_active_fight": len(getattr(director_state, 'active_fights', {})) > 0,
            "fight_intensity": max(
                [f.get('intensity_score', 0) for f in getattr(director_state, 'active_fights', {}).values()],
                default=0
            ),
            "is_clutch": any(
                f.get('is_clutch', False) for f in getattr(director_state, 'active_fights', {}).values()
            ),
            "has_replay_queued": len(getattr(director_state, 'replay_buffer', [])) > 0,
            "match_time": getattr(director_state, 'match_time_seconds', 0),
            "teams_alive": getattr(director_state, 'teams_alive', 16),
            "zone_phase": getattr(director_state, 'zone_phase', 0),
            "has_contested_landing": getattr(director_state, 'match_time_seconds', 0) < 120,
            "zone_changed": False,  # Would track zone changes
            "has_star_player_alive": False,  # Would check star players
            "match_ended": getattr(director_state, 'teams_alive', 16) <= 1,
            "scene_duration": getattr(
                getattr(director_state, 'current_scene', None), 'duration', 0
            ) if hasattr(director_state, 'current_scene') else 0,
        }
    
    def _evaluate_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        """Evaluate a playbook rule condition"""
        try:
            # Safe evaluation of condition string
            return eval(condition, {"__builtins__": {}}, context)
        except Exception as e:
            self._log.error(f"Condition evaluation error: {condition} - {e}")
            return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLAYBOOK MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_custom_playbook(self, playbook: Playbook) -> None:
        """Add a custom playbook"""
        self.playbooks[playbook.playbook_type] = playbook
        self._log.info(f"Custom playbook added: {playbook.name}")
    
    def add_rule(self, playbook_type: PlaybookType, rule: PlaybookRule) -> None:
        """Add a rule to a playbook"""
        if playbook_type in self.playbooks:
            self.playbooks[playbook_type].rules.append(rule)
            self._log.info(f"Rule added to {playbook_type.value}: {rule.description}")
    
    def enable_playbook(self, playbook_type: PlaybookType) -> None:
        """Enable a playbook"""
        if playbook_type in self.playbooks:
            self.playbooks[playbook_type].is_active = True
            self._log.info(f"Playbook enabled: {playbook_type.value}")
    
    def disable_playbook(self, playbook_type: PlaybookType) -> None:
        """Disable a playbook"""
        if playbook_type in self.playbooks:
            self.playbooks[playbook_type].is_active = False
            self._log.info(f"Playbook disabled: {playbook_type.value}")
    
    def get_playbook_summary(self) -> Dict[str, Any]:
        """Get summary of all playbooks"""
        return {
            "active_playbook": self.active_playbook.value if self.active_playbook else None,
            "playbooks": {
                pt.value: {
                    "name": pb.name,
                    "active": pb.is_active,
                    "rules_count": len(pb.rules),
                    "priority": pb.priority,
                }
                for pt, pb in self.playbooks.items()
            },
            "history": list(self.playbook_history),
        }
    
    def reset(self) -> None:
        """Reset playbook manager"""
        self.active_playbook = None
        self.action_cooldowns.clear()
        self.playbook_history.clear()
        self._log.info("Playbook manager reset")