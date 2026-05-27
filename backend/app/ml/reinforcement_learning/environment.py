# backend/app/ml/reinforcement_learning/environment.py
"""
AEGIS AI - Broadcast Environment
OpenAI Gym-compatible environment for training the observer RL agent.
Simulates the broadcast decision-making process.
"""

import time
import random
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import structlog
import numpy as np
import gym
from gym import spaces

logger = structlog.get_logger("aegis.ml.rl.environment")


@dataclass
class GameState:
    """Complete game state for the RL environment"""
    # Match info
    match_time: float = 0.0
    zone_phase: int = 1
    teams_alive: int = 16
    players_alive: int = 64
    
    # Active fights
    active_fights: List[Dict[str, Any]] = field(default_factory=list)
    highest_intensity_fight: Optional[Dict[str, Any]] = None
    
    # Clutch situations
    active_clutches: List[Dict[str, Any]] = field(default_factory=list)
    
    # Team states
    team_states: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Current broadcast
    current_feed: str = "team_overview"
    current_team: str = ""
    current_player: str = ""
    scene_duration: float = 0.0
    
    # Recent events
    recent_kills: List[Dict[str, Any]] = field(default_factory=list)
    recent_knocks: List[Dict[str, Any]] = field(default_factory=list)
    
    # Star players
    star_players: List[Dict[str, Any]] = field(default_factory=list)
    
    # Replay queue
    replay_queue: List[Dict[str, Any]] = field(default_factory=list)
    
    # Rivalry matchups
    active_rivalries: List[Dict[str, Any]] = field(default_factory=list)
    
    # Storylines
    active_storylines: List[str] = field(default_factory=list)


@dataclass
class EnvConfig:
    """Environment configuration"""
    # Episode
    max_steps: int = 1000  # Steps per episode (10 steps/second = 100 seconds)
    step_time: float = 0.1  # Seconds per step
    
    # State
    state_vector_size: int = 128
    
    # Actions
    num_actions: int = 9  # CameraAction enum size
    
    # Rewards
    reward_scale: float = 1.0
    
    # Simulation
    fight_probability: float = 0.3  # Probability of fight per step
    clutch_probability: float = 0.05
    kill_probability: float = 0.1
    
    # Teams
    num_teams: int = 16
    priority_teams: List[int] = field(default_factory=list)


class BroadcastEnvironment(gym.Env):
    """
    Gym environment for training the observer RL agent.
    
    State Space (128-dim vector):
    - Match context (10 features)
    - Active fights (20 features)
    - Team states (16 * 4 features)
    - Current broadcast (10 features)
    - Recent events (10 features)
    
    Action Space (9 actions):
    - Stay current, Switch teams, Show fight, Show clutch,
      Show overview, Show replay, Follow star, Cinematic
    
    Rewards:
    - Show important fight: +10
    - Show clutch: +20
    - Show winning team: +5
    - Good variety: +2
    - Bad switch: -5
    - Miss clutch: -20
    - Show replay at right time: +8
    """
    
    metadata = {"render_modes": ["console"], "render_fps": 10}
    
    def __init__(self, config: Optional[EnvConfig] = None):
        super().__init__()
        
        self.config = config or EnvConfig()
        
        # Action space: 9 discrete actions
        self.action_space = spaces.Discrete(self.config.num_actions)
        
        # Observation space: 128-dim feature vector
        self.observation_space = spaces.Box(
            low=-10.0, high=10.0,
            shape=(self.config.state_vector_size,),
            dtype=np.float32,
        )
        
        # Game state
        self.state: Optional[GameState] = None
        self.current_step: int = 0
        self.total_reward: float = 0.0
        self.episode_rewards: List[float] = []
        
        # Random seed
        self.rng = np.random.default_rng()
        
        self._log = logger.bind(component="broadcast_environment")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # GYM INTERFACE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Reset environment to initial state.
        
        Returns:
            Tuple of (observation, info)
        """
        
        super().reset(seed=seed)
        
        # Initialize game state
        self.state = self._generate_initial_state()
        self.current_step = 0
        self.total_reward = 0.0
        
        # Get initial observation
        observation = self._build_observation()
        
        info = {
            "match_time": self.state.match_time,
            "teams_alive": self.state.teams_alive,
        }
        
        return observation, info
    
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """
        Execute one step in the environment.
        
        Args:
            action: Action index (0-8)
            
        Returns:
            Tuple of (observation, reward, terminated, truncated, info)
        """
        
        self.current_step += 1
        
        # Execute action
        self._execute_action(action)
        
        # Update game state
        self._update_game_state()
        
        # Calculate reward
        reward = self._calculate_reward(action)
        self.total_reward += reward
        
        # Build observation
        observation = self._build_observation()
        
        # Check termination
        terminated = self.current_step >= self.config.max_steps
        truncated = False  # Could be used for time limits
        
        info = {
            "match_time": self.state.match_time,
            "teams_alive": self.state.teams_alive,
            "active_fights": len(self.state.active_fights),
            "current_feed": self.state.current_feed,
            "total_reward": self.total_reward,
        }
        
        if terminated:
            self.episode_rewards.append(self.total_reward)
        
        return observation, reward, terminated, truncated, info
    
    def render(self) -> None:
        """Render environment state"""
        if self.state:
            print(f"\n{'='*50}")
            print(f"Step: {self.current_step} | Time: {self.state.match_time:.0f}s")
            print(f"Zone: {self.state.zone_phase} | Teams: {self.state.teams_alive}")
            print(f"Active Fights: {len(self.state.active_fights)}")
            print(f"Current Feed: {self.state.current_feed}")
            print(f"Scene Duration: {self.state.scene_duration:.1f}s")
            print(f"Total Reward: {self.total_reward:.2f}")
            print(f"{'='*50}\n")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _generate_initial_state(self) -> GameState:
        """Generate initial game state"""
        
        state = GameState()
        
        # Generate team states
        for i in range(self.config.num_teams):
            team_id = f"team_{i+1:03d}"
            state.team_states[team_id] = {
                "players_alive": 4,
                "total_hp": 400.0,
                "kills": 0,
                "damage": 0,
                "is_fighting": False,
                "is_clutch": False,
                "position": (random.uniform(0, 8000), random.uniform(0, 8000)),
                "cover_quality": random.uniform(30, 70),
            }
        
        return state
    
    def _update_game_state(self) -> None:
        """Update game state for next step"""
        
        dt = self.config.step_time
        self.state.match_time += dt
        
        # Update zone
        if self.state.match_time > 180 and self.state.zone_phase < 8:
            if random.random() < 0.01:  # Zone shrinks occasionally
                self.state.zone_phase = min(8, self.state.zone_phase + 1)
        
        # Generate random fights
        if random.random() < self.config.fight_probability * dt:
            self._generate_fight()
        
        # Generate clutch situations
        if random.random() < self.config.clutch_probability * dt:
            self._generate_clutch()
        
        # Resolve existing fights
        self._resolve_fights()
        
        # Update current scene duration
        self.state.scene_duration += dt
        
        # Decrease teams alive over time
        if self.state.match_time > 60 and random.random() < 0.005:
            self.state.teams_alive = max(1, self.state.teams_alive - 1)
            self.state.players_alive = self.state.teams_alive * 4
    
    def _generate_fight(self) -> None:
        """Generate a random fight"""
        
        if self.state.teams_alive < 2:
            return
        
        # Pick two random teams
        team_ids = list(self.state.team_states.keys())
        team_a = random.choice(team_ids)
        team_b = random.choice([t for t in team_ids if t != team_a])
        
        intensity = random.uniform(30, 90)
        
        fight = {
            "fight_id": f"fight_{int(time.time()*1000)}",
            "team_a": team_a,
            "team_b": team_b,
            "intensity": intensity,
            "start_time": self.state.match_time,
            "duration": 0.0,
            "is_active": True,
            "is_clutch": False,
            "entertainment_score": intensity + random.uniform(0, 20),
        }
        
        self.state.active_fights.append(fight)
        self.state.team_states[team_a]["is_fighting"] = True
        self.state.team_states[team_b]["is_fighting"] = True
        
        # Update highest intensity fight
        self.state.highest_intensity_fight = max(
            self.state.active_fights,
            key=lambda f: f["intensity"],
        ) if self.state.active_fights else None
    
    def _generate_clutch(self) -> None:
        """Generate a clutch situation"""
        
        team_ids = list(self.state.team_states.keys())
        team = random.choice(team_ids)
        
        self.state.team_states[team]["is_clutch"] = True
        self.state.team_states[team]["players_alive"] = 1
        
        self.state.active_clutches.append({
            "team_id": team,
            "players_alive": 1,
            "enemies": random.randint(2, 4),
            "probability": random.uniform(0.05, 0.3),
            "start_time": self.state.match_time,
        })
    
    def _resolve_fights(self) -> None:
        """Resolve ongoing fights"""
        
        for fight in self.state.active_fights[:]:
            fight["duration"] = self.state.match_time - fight["start_time"]
            
            # Fights end after 10-30 seconds
            if fight["duration"] > random.uniform(10, 30):
                fight["is_active"] = False
                
                # Determine winner
                winner = random.choice([fight["team_a"], fight["team_b"]])
                fight["winner"] = winner
                
                # Update team states
                self.state.team_states[fight["team_a"]]["is_fighting"] = False
                self.state.team_states[fight["team_b"]]["is_fighting"] = False
                
                # Generate kills
                fight["total_kills"] = random.randint(1, 4)
        
        # Remove resolved fights
        self.state.active_fights = [
            f for f in self.state.active_fights if f["is_active"]
        ]
    
    def _execute_action(self, action: int) -> None:
        """Execute camera action"""
        
        if action == 0:  # STAY_CURRENT
            pass  # No change
        elif action == 1:  # SWITCH_TEAM_A
            if self.state.highest_intensity_fight:
                self.state.current_team = self.state.highest_intensity_fight["team_a"]
                self.state.current_feed = "team_pov"
                self.state.scene_duration = 0.0
        elif action == 2:  # SWITCH_TEAM_B
            if self.state.highest_intensity_fight:
                self.state.current_team = self.state.highest_intensity_fight["team_b"]
                self.state.current_feed = "team_pov"
                self.state.scene_duration = 0.0
        elif action == 3:  # SWITCH_TO_FIGHT
            if self.state.active_fights:
                self.state.current_feed = "fight_coverage"
                self.state.scene_duration = 0.0
        elif action == 4:  # SWITCH_TO_CLUTCH
            if self.state.active_clutches:
                self.state.current_team = self.state.active_clutches[0]["team_id"]
                self.state.current_feed = "clutch_focus"
                self.state.scene_duration = 0.0
        elif action == 5:  # SWITCH_OVERVIEW
            self.state.current_feed = "map_overview"
            self.state.scene_duration = 0.0
        elif action == 6:  # SHOW_REPLAY
            if self.state.replay_queue:
                self.state.current_feed = "replay"
                self.state.scene_duration = 0.0
        elif action == 7:  # FOLLOW_STAR
            if self.state.star_players:
                star = random.choice(self.state.star_players)
                self.state.current_player = star.get("name", "")
                self.state.current_feed = "star_player"
                self.state.scene_duration = 0.0
        elif action == 8:  # CINEMATIC_SHOT
            self.state.current_feed = "cinematic"
            self.state.scene_duration = 0.0
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # OBSERVATION BUILDING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _build_observation(self) -> np.ndarray:
        """Build state vector for RL agent"""
        
        features = []
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Match context (10 features)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        features.extend([
            self.state.match_time / 1800,  # Normalize to 30 min
            self.state.zone_phase / 8,
            self.state.teams_alive / 16,
            self.state.players_alive / 64,
        ])
        features.extend([0.0] * 6)  # Padding to 10
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Active fights (20 features)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        for i in range(min(5, len(self.state.active_fights))):
            fight = self.state.active_fights[i]
            features.extend([
                fight["intensity"] / 100,
                fight["duration"] / 60,
                float(fight["is_active"]),
                fight.get("entertainment_score", 50) / 100,
            ])
        features.extend([0.0] * (20 - min(5, len(self.state.active_fights)) * 4))
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Current broadcast (10 features)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        feed_types = [
            "team_pov", "fight_coverage", "clutch_focus",
            "map_overview", "replay", "star_player", "cinematic",
        ]
        for ft in feed_types:
            features.append(1.0 if self.state.current_feed == ft else 0.0)
        features.append(self.state.scene_duration / 30)  # Normalize to 30s
        features.extend([0.0] * 2)  # Padding to 10
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Team states (first 5 teams - 20 features)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        for i, (team_id, team) in enumerate(list(self.state.team_states.items())[:5]):
            features.extend([
                team["players_alive"] / 4,
                team["total_hp"] / 400,
                float(team["is_fighting"]),
                float(team.get("is_clutch", False)),
            ])
        features.extend([0.0] * (20 - min(5, len(self.state.team_states)) * 4))
        
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Padding to full state size
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        while len(features) < self.config.state_vector_size:
            features.append(0.0)
        
        features = features[:self.config.state_vector_size]
        
        return np.array(features, dtype=np.float32)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REWARD CALCULATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _calculate_reward(self, action: int) -> float:
        """Calculate reward for the current action"""
        
        from .reward_function import RewardFunction
        
        # Use the reward function module
        reward_fn = RewardFunction()
        
        return reward_fn.calculate_reward(
            action=action,
            game_state=self.state,
            scene_duration=self.state.scene_duration,
            previous_feed=getattr(self, '_previous_feed', ""),
        )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get environment statistics"""
        recent = self.episode_rewards[-10:] if self.episode_rewards else [0]
        
        return {
            "episodes_completed": len(self.episode_rewards),
            "avg_reward": round(np.mean(recent), 2),
            "max_reward": round(max(self.episode_rewards) if self.episode_rewards else 0, 2),
            "state_size": self.config.state_vector_size,
            "action_size": self.config.num_actions,
        }
    
    def close(self) -> None:
        """Clean up environment"""
        pass