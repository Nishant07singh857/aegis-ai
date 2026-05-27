# backend/app/ml/reinforcement_learning/reward_function.py
"""
AEGIS AI - Reward Function
Defines the reward structure for training the observer RL agent.
Balances multiple objectives for professional broadcast quality.
"""

from typing import Dict, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.ml.rl.reward")


class RewardComponent(str, Enum):
    """Individual reward components"""
    SHOW_IMPORTANT_FIGHT = "show_important_fight"
    SHOW_CLUTCH = "show_clutch"
    SHOW_WINNING_MOMENT = "show_winning_moment"
    GOOD_VARIETY = "good_variety"
    AVOID_RAPID_SWITCHING = "avoid_rapid_switching"
    SHOW_REPLAY_TIMELY = "show_replay_timely"
    FOLLOW_STORYLINE = "follow_storyline"
    COVER_STAR_PLAYER = "cover_star_player"
    SMOOTH_TRANSITIONS = "smooth_transitions"
    MINIMUM_SCENE_TIME = "minimum_scene_time"


@dataclass
class RewardConfig:
    """Reward function configuration"""
    # Component weights
    weights: Dict[RewardComponent, float] = field(default_factory=lambda: {
        RewardComponent.SHOW_IMPORTANT_FIGHT: 15.0,
        RewardComponent.SHOW_CLUTCH: 25.0,
        RewardComponent.SHOW_WINNING_MOMENT: 10.0,
        RewardComponent.GOOD_VARIETY: 3.0,
        RewardComponent.AVOID_RAPID_SWITCHING: -8.0,
        RewardComponent.SHOW_REPLAY_TIMELY: 8.0,
        RewardComponent.FOLLOW_STORYLINE: 5.0,
        RewardComponent.COVER_STAR_PLAYER: 4.0,
        RewardComponent.SMOOTH_TRANSITIONS: 2.0,
        RewardComponent.MINIMUM_SCENE_TIME: -5.0,
    })
    
    # Thresholds
    fight_intensity_threshold: float = 50.0
    min_scene_duration: float = 8.0
    max_switches_per_minute: int = 8
    replay_cooldown: float = 15.0
    
    # Scaling
    reward_scale: float = 1.0


@dataclass
class RewardComponents:
    """Breakdown of reward components"""
    fight_reward: float = 0.0
    clutch_reward: float = 0.0
    variety_reward: float = 0.0
    switching_penalty: float = 0.0
    replay_reward: float = 0.0
    storyline_reward: float = 0.0
    star_player_reward: float = 0.0
    transition_reward: float = 0.0
    scene_time_penalty: float = 0.0
    total: float = 0.0


class RewardFunction:
    """
    Multi-objective reward function for observer RL training.
    
    Objectives:
    1. Show important fights (highest weight)
    2. Show clutch situations (critical)
    3. Show winning/deciding moments
    4. Maintain viewing variety
    5. Avoid chaotic rapid switching
    6. Show replays at appropriate times
    7. Follow tournament storylines
    8. Cover star players
    9. Use smooth transitions
    10. Respect minimum scene times
    
    The reward function balances these objectives to create
    professional-quality broadcast behavior.
    """
    
    def __init__(self, config: Optional[RewardConfig] = None):
        self.config = config or RewardConfig()
        self._log = logger.bind(component="reward_function")
    
    def calculate_reward(
        self,
        action: int,
        game_state: Any,
        scene_duration: float = 0.0,
        previous_feed: str = "",
    ) -> float:
        """
        Calculate total reward for an action.
        
        Args:
            action: Action index (0-8)
            game_state: Current GameState
            scene_duration: Duration of current scene
            previous_feed: Previous feed type
            
        Returns:
            Total reward value
        """
        
        components = RewardComponents()
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 1. Show important fight reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.fight_reward = self._calculate_fight_reward(
            action, game_state, scene_duration
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 2. Clutch reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.clutch_reward = self._calculate_clutch_reward(
            action, game_state
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 3. Variety reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.variety_reward = self._calculate_variety_reward(
            action, game_state, previous_feed
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 4. Switching penalty
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.switching_penalty = self._calculate_switching_penalty(
            action, scene_duration
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 5. Replay reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.replay_reward = self._calculate_replay_reward(
            action, game_state
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 6. Storyline reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.storyline_reward = self._calculate_storyline_reward(
            action, game_state
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 7. Star player reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.star_player_reward = self._calculate_star_player_reward(
            action, game_state
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 8. Transition smoothness reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.transition_reward = self._calculate_transition_reward(
            action, previous_feed, game_state
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 9. Scene time penalty
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.scene_time_penalty = self._calculate_scene_time_penalty(
            action, scene_duration
        )
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Total reward
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        components.total = (
            components.fight_reward +
            components.clutch_reward +
            components.variety_reward +
            components.switching_penalty +
            components.replay_reward +
            components.storyline_reward +
            components.star_player_reward +
            components.transition_reward +
            components.scene_time_penalty
        )
        
        return components.total * self.config.reward_scale
    
    def _calculate_fight_reward(
        self,
        action: int,
        state: Any,
        scene_duration: float,
    ) -> float:
        """Reward for showing important fights"""
        
        weight = self.config.weights[RewardComponent.SHOW_IMPORTANT_FIGHT]
        
        # Actions that show fights: 1, 2, 3
        if action in [1, 2, 3] and state.active_fights:
            # Find the most important fight
            best_fight = max(
                state.active_fights,
                key=lambda f: f.get("intensity", 0),
            )
            
            intensity = best_fight.get("intensity", 0)
            
            if intensity > self.config.fight_intensity_threshold:
                # Higher reward for higher intensity
                return weight * (intensity / 100)
            elif intensity > 30:
                return weight * 0.5
            else:
                return weight * 0.2
        
        # Penalty for NOT showing an important fight
        if action == 0 and state.active_fights:  # Staying on current
            best_fight = max(
                state.active_fights,
                key=lambda f: f.get("intensity", 0),
            )
            
            if best_fight.get("intensity", 0) > 70:
                return -weight * 0.5  # Missing a big fight
        
        return 0.0
    
    def _calculate_clutch_reward(
        self,
        action: int,
        state: Any,
    ) -> float:
        """Reward for showing clutch situations"""
        
        weight = self.config.weights[RewardComponent.SHOW_CLUTCH]
        
        # Action 4 is SWITCH_TO_CLUTCH
        if action == 4 and state.active_clutches:
            return weight
        
        # Penalty for missing clutch
        if action != 4 and state.active_clutches:
            return -weight * 0.8
        
        return 0.0
    
    def _calculate_variety_reward(
        self,
        action: int,
        state: Any,
        previous_feed: str,
    ) -> float:
        """Reward for good viewing variety"""
        
        weight = self.config.weights[RewardComponent.GOOD_VARIETY]
        
        # Reward switching to different types of content
        current_feed = state.current_feed
        
        if previous_feed and current_feed != previous_feed:
            # Bonus for variety
            variety_bonus = weight * 0.3
            
            # Extra bonus for switching between different categories
            action_categories = {
                "fight_coverage": "action",
                "clutch_focus": "action",
                "team_pov": "action",
                "map_overview": "analysis",
                "replay": "analysis",
                "cinematic": "cinematic",
            }
            
            prev_cat = action_categories.get(previous_feed, "other")
            curr_cat = action_categories.get(current_feed, "other")
            
            if prev_cat != curr_cat:
                variety_bonus *= 1.5
            
            return variety_bonus
        
        # Small penalty for staying on same feed too long
        if state.scene_duration > 20:
            return -weight * 0.2
        
        return 0.0
    
    def _calculate_switching_penalty(
        self,
        action: int,
        scene_duration: float,
    ) -> float:
        """Penalty for rapid switching"""
        
        weight = self.config.weights[RewardComponent.AVOID_RAPID_SWITCHING]
        
        # Action 0 is STAY_CURRENT (no switch)
        if action == 0:
            return 0.0
        
        # Penalty for switching too fast
        if scene_duration < self.config.min_scene_duration:
            return weight * (1 - scene_duration / self.config.min_scene_duration)
        
        return 0.0
    
    def _calculate_replay_reward(
        self,
        action: int,
        state: Any,
    ) -> float:
        """Reward for showing replays at right time"""
        
        weight = self.config.weights[RewardComponent.SHOW_REPLAY_TIMELY]
        
        # Action 6 is SHOW_REPLAY
        if action == 6:
            # Reward only if there's a good replay to show
            if state.replay_queue:
                # Higher reward for more exciting replays
                best_replay = max(
                    state.replay_queue,
                    key=lambda r: r.get("priority", 0),
                    default={"priority": 50},
                )
                
                return weight * (best_replay.get("priority", 50) / 100)
            else:
                return -weight * 0.5  # Showing replay with nothing to show
        
        return 0.0
    
    def _calculate_storyline_reward(
        self,
        action: int,
        state: Any,
    ) -> float:
        """Reward for following storylines"""
        
        weight = self.config.weights[RewardComponent.FOLLOW_STORYLINE]
        
        if not state.active_storylines:
            return 0.0
        
        # Reward for showing content related to active storylines
        if state.active_rivalries and action in [1, 2, 3]:
            # Check if current fight involves rivalry teams
            for fight in state.active_fights:
                if fight.get("is_rivalry", False):
                    return weight
        
        return 0.0
    
    def _calculate_star_player_reward(
        self,
        action: int,
        state: Any,
    ) -> float:
        """Reward for covering star players"""
        
        weight = self.config.weights[RewardComponent.COVER_STAR_PLAYER]
        
        # Action 7 is FOLLOW_STAR
        if action == 7 and state.star_players:
            return weight
        
        return 0.0
    
    def _calculate_transition_reward(
        self,
        action: int,
        previous_feed: str,
        state: Any,
    ) -> float:
        """Reward for smooth transitions"""
        
        weight = self.config.weights[RewardComponent.SMOOTH_TRANSITIONS]
        
        if action == 0:
            return 0.0  # No transition
        
        # Reward natural transitions
        natural_transitions = {
            "fight_coverage": ["team_pov", "replay", "map_overview"],
            "team_pov": ["fight_coverage", "map_overview", "replay"],
            "clutch_focus": ["replay", "team_pov"],
            "map_overview": ["team_pov", "fight_coverage", "cinematic"],
            "replay": ["fight_coverage", "team_pov"],
        }
        
        current_feed = state.current_feed
        
        if previous_feed and current_feed in natural_transitions.get(previous_feed, []):
            return weight * 0.5
        
        return 0.0
    
    def _calculate_scene_time_penalty(
        self,
        action: int,
        scene_duration: float,
    ) -> float:
        """Penalty for violating minimum scene time"""
        
        weight = self.config.weights[RewardComponent.MINIMUM_SCENE_TIME]
        
        if action != 0 and scene_duration < self.config.min_scene_duration:
            return weight
        
        return 0.0
    
    def get_stats(self) -> Dict[str, Any]:
        """Get reward function statistics"""
        return {
            "weights": {
                comp.value: weight
                for comp, weight in self.config.weights.items()
            },
            "thresholds": {
                "fight_intensity": self.config.fight_intensity_threshold,
                "min_scene_duration": self.config.min_scene_duration,
                "max_switches_per_minute": self.config.max_switches_per_minute,
            },
        }