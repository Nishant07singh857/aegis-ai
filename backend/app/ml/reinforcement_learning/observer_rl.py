# backend/app/ml/reinforcement_learning/observer_rl.py
"""
AEGIS AI - Observer RL Agent
Reinforcement learning agent for autonomous camera switching.
Learns optimal broadcast direction through experience.
"""

import random
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim

logger = structlog.get_logger("aegis.ml.rl.observer")


class CameraAction(str, Enum):
    """Camera switching actions"""
    STAY_CURRENT = "stay_current"          # Keep current feed
    SWITCH_TEAM_A = "switch_team_a"        # Switch to Team A
    SWITCH_TEAM_B = "switch_team_b"        # Switch to Team B
    SWITCH_TO_FIGHT = "switch_to_fight"    # Switch to active fight
    SWITCH_TO_CLUTCH = "switch_to_clutch"  # Switch to clutch
    SWITCH_OVERVIEW = "switch_overview"    # Show map overview
    SHOW_REPLAY = "show_replay"            # Show replay
    FOLLOW_STAR = "follow_star"           # Follow star player
    CINEMATIC_SHOT = "cinematic_shot"      # Cinematic camera


@dataclass
class RLObserverConfig:
    """RL Observer configuration"""
    # Network
    hidden_size: int = 256
    num_layers: int = 3
    dropout: float = 0.1
    
    # Training
    learning_rate: float = 3e-4
    batch_size: int = 64
    gamma: float = 0.99           # Discount factor
    tau: float = 0.005            # Target network update rate
    epsilon_start: float = 1.0    # Exploration start
    epsilon_end: float = 0.05     # Exploration minimum
    epsilon_decay: float = 0.9995 # Decay per step
    
    # Memory
    replay_buffer_size: int = 100000
    min_replay_size: int = 1000
    
    # Actions
    num_actions: int = len(CameraAction)
    action_space: List[CameraAction] = field(default_factory=lambda: list(CameraAction))
    
    # State
    state_size: int = 128  # Feature vector size
    
    # Rewards
    use_curiosity: bool = True   # Intrinsic motivation
    curiosity_weight: float = 0.1
    
    # Saving
    model_path: str = "models/observer_rl.pt"
    save_interval: int = 1000


@dataclass
class Experience:
    """Experience replay tuple"""
    state: np.ndarray
    action: int
    reward: float
    next_state: np.ndarray
    done: bool
    priority: float = 1.0


class DQNNetwork(nn.Module):
    """Deep Q-Network for observer decisions"""
    
    def __init__(self, state_size: int, action_size: int, hidden_size: int = 256):
        super().__init__()
        
        self.network = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.LayerNorm(hidden_size),
            nn.Dropout(0.1),
            
            nn.Linear(hidden_size, hidden_size),
            nn.ReLU(),
            nn.LayerNorm(hidden_size),
            nn.Dropout(0.1),
            
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.LayerNorm(hidden_size // 2),
            
            nn.Linear(hidden_size // 2, action_size),
        )
    
    def forward(self, state: torch.Tensor) -> torch.Tensor:
        return self.network(state)


class DuelingDQN(nn.Module):
    """Dueling DQN with separate value and advantage streams"""
    
    def __init__(self, state_size: int, action_size: int, hidden_size: int = 256):
        super().__init__()
        
        # Shared feature layer
        self.feature_layer = nn.Sequential(
            nn.Linear(state_size, hidden_size),
            nn.ReLU(),
            nn.LayerNorm(hidden_size),
        )
        
        # Value stream
        self.value_stream = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, 1),
        )
        
        # Advantage stream
        self.advantage_stream = nn.Sequential(
            nn.Linear(hidden_size, hidden_size // 2),
            nn.ReLU(),
            nn.Linear(hidden_size // 2, action_size),
        )
    
    def forward(self, state: torch.Tensor) -> torch.Tensor:
        features = self.feature_layer(state)
        
        value = self.value_stream(features)
        advantage = self.advantage_stream(features)
        
        # Combine: Q(s,a) = V(s) + A(s,a) - mean(A(s,:))
        return value + advantage - advantage.mean(dim=1, keepdim=True)


class PrioritizedReplayBuffer:
    """Prioritized Experience Replay buffer"""
    
    def __init__(self, capacity: int, alpha: float = 0.6):
        self.capacity = capacity
        self.alpha = alpha
        self.buffer: deque = deque(maxlen=capacity)
        self.priorities: deque = deque(maxlen=capacity)
        self.epsilon = 1e-6
    
    def add(self, experience: Experience) -> None:
        """Add experience with priority"""
        max_priority = max(self.priorities) if self.priorities else 1.0
        self.buffer.append(experience)
        self.priorities.append(max_priority)
    
    def sample(self, batch_size: int, beta: float = 0.4) -> Tuple[List[Experience], np.ndarray, np.ndarray]:
        """Sample batch with priorities"""
        
        if len(self.buffer) == 0:
            return [], np.array([]), np.array([])
        
        # Calculate probabilities
        priorities = np.array(self.priorities)
        probs = priorities ** self.alpha
        probs /= probs.sum()
        
        # Sample indices
        indices = np.random.choice(len(self.buffer), min(batch_size, len(self.buffer)), p=probs)
        
        # Get experiences
        experiences = [self.buffer[i] for i in indices]
        
        # Calculate importance weights
        total = len(self.buffer)
        weights = (total * probs[indices]) ** (-beta)
        weights /= weights.max()
        
        return experiences, indices, weights
    
    def update_priorities(self, indices: np.ndarray, td_errors: np.ndarray) -> None:
        """Update priorities based on TD errors"""
        for idx, td_error in zip(indices, td_errors):
            self.priorities[idx] = abs(td_error) + self.epsilon
    
    def __len__(self) -> int:
        return len(self.buffer)


class ObserverRL:
    """
    Reinforcement Learning agent for autonomous camera switching.
    
    Uses Dueling Double DQN with:
    - Prioritized Experience Replay
    - Target network with soft updates
    - Curiosity-driven exploration
    - Epsilon-greedy with decay
    
    Learns to:
    - Show the most important action at any moment
    - Balance fight coverage vs variety
    - Time replays appropriately
    - Follow storylines
    - Create professional broadcast flow
    """
    
    def __init__(self, config: Optional[RLObserverConfig] = None):
        self.config = config or RLObserverConfig()
        
        # Networks
        self.policy_net: Optional[DuelingDQN] = None
        self.target_net: Optional[DuelingDQN] = None
        
        # Optimizer
        self.optimizer: Optional[optim.Adam] = None
        
        # Replay buffer
        self.replay_buffer = PrioritizedReplayBuffer(
            self.config.replay_buffer_size
        )
        
        # Exploration
        self.epsilon = self.config.epsilon_start
        self.total_steps = 0
        
        # Curiosity (intrinsic motivation)
        self.curiosity_net: Optional[nn.Module] = None
        self.curiosity_optimizer: Optional[optim.Adam] = None
        
        # Statistics
        self._total_episodes = 0
        self._total_rewards: List[float] = []
        self._avg_loss: float = 0.0
        self._best_reward: float = float('-inf')
        
        # Device
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        
        # Initialize networks
        self._initialize_networks()
        
        self._log = logger.bind(component="observer_rl")
    
    def _initialize_networks(self) -> None:
        """Initialize DQN networks"""
        
        self.policy_net = DuelingDQN(
            self.config.state_size,
            self.config.num_actions,
            self.config.hidden_size,
        ).to(self.device)
        
        self.target_net = DuelingDQN(
            self.config.state_size,
            self.config.num_actions,
            self.config.hidden_size,
        ).to(self.device)
        
        # Copy weights
        self.target_net.load_state_dict(self.policy_net.state_dict())
        
        self.optimizer = optim.Adam(
            self.policy_net.parameters(),
            lr=self.config.learning_rate,
        )
        
        self._log.info(
            f"Networks initialized: {self.config.state_size} → "
            f"{self.config.hidden_size} → {self.config.num_actions}"
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ACTION SELECTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def select_action(
        self,
        state: np.ndarray,
        training: bool = True,
        valid_actions: Optional[List[int]] = None,
    ) -> Tuple[int, CameraAction]:
        """
        Select camera action using epsilon-greedy policy.
        
        Args:
            state: Current game state features
            training: Whether in training mode (use exploration)
            valid_actions: List of valid action indices
            
        Returns:
            Tuple of (action_index, CameraAction)
        """
        
        # Epsilon-greedy
        if training and random.random() < self.epsilon:
            # Random action
            if valid_actions:
                action_idx = random.choice(valid_actions)
            else:
                action_idx = random.randrange(self.config.num_actions)
        else:
            # Greedy action
            with torch.no_grad():
                state_tensor = torch.FloatTensor(state).unsqueeze(0).to(self.device)
                q_values = self.policy_net(state_tensor)
                
                if valid_actions:
                    # Mask invalid actions
                    mask = torch.ones_like(q_values) * -1e9
                    for a in valid_actions:
                        mask[0, a] = 0
                    q_values = q_values + mask
                
                action_idx = q_values.argmax(dim=1).item()
        
        action = self.config.action_space[action_idx]
        self.total_steps += 1
        
        return action_idx, action
    
    def select_action_greedy(self, state: np.ndarray) -> Tuple[int, CameraAction]:
        """Select best action without exploration"""
        return self.select_action(state, training=False)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TRAINING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def store_experience(
        self,
        state: np.ndarray,
        action: int,
        reward: float,
        next_state: np.ndarray,
        done: bool,
    ) -> None:
        """Store experience in replay buffer"""
        
        experience = Experience(
            state=state,
            action=action,
            reward=reward,
            next_state=next_state,
            done=done,
        )
        
        self.replay_buffer.add(experience)
    
    def train_step(self, beta: float = 0.4) -> Optional[float]:
        """
        Perform one training step.
        
        Args:
            beta: Importance sampling beta
            
        Returns:
            Loss value or None if not enough samples
        """
        
        if len(self.replay_buffer) < self.config.min_replay_size:
            return None
        
        # Sample batch
        experiences, indices, weights = self.replay_buffer.sample(
            self.config.batch_size, beta
        )
        
        if not experiences:
            return None
        
        # Prepare batch
        states = torch.FloatTensor(
            np.array([e.state for e in experiences])
        ).to(self.device)
        
        actions = torch.LongTensor(
            [e.action for e in experiences]
        ).unsqueeze(1).to(self.device)
        
        rewards = torch.FloatTensor(
            [e.reward for e in experiences]
        ).unsqueeze(1).to(self.device)
        
        next_states = torch.FloatTensor(
            np.array([e.next_state for e in experiences])
        ).to(self.device)
        
        dones = torch.FloatTensor(
            [float(e.done) for e in experiences]
        ).unsqueeze(1).to(self.device)
        
        weights_tensor = torch.FloatTensor(weights).unsqueeze(1).to(self.device)
        
        # Current Q values
        current_q = self.policy_net(states).gather(1, actions)
        
        # Double DQN: select action with policy net, evaluate with target net
        with torch.no_grad():
            next_actions = self.policy_net(next_states).argmax(dim=1, keepdim=True)
            next_q = self.target_net(next_states).gather(1, next_actions)
            target_q = rewards + self.config.gamma * next_q * (1 - dones)
        
        # Compute loss with importance weights
        td_errors = (target_q - current_q).detach().cpu().numpy()
        loss = (weights_tensor * (current_q - target_q) ** 2).mean()
        
        # Curiosity bonus (intrinsic reward for novel states)
        if self.config.use_curiosity:
            curiosity_loss = self._compute_curiosity(states, next_states)
            loss += self.config.curiosity_weight * curiosity_loss
        
        # Optimize
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), 10.0)
        self.optimizer.step()
        
        # Update priorities
        self.replay_buffer.update_priorities(indices, td_errors.flatten())
        
        # Soft update target network
        self._soft_update_target()
        
        # Decay epsilon
        self.epsilon = max(
            self.config.epsilon_end,
            self.epsilon * self.config.epsilon_decay,
        )
        
        loss_value = loss.item()
        self._avg_loss = 0.95 * self._avg_loss + 0.05 * loss_value
        
        return loss_value
    
    def _soft_update_target(self) -> None:
        """Soft update target network parameters"""
        for target_param, policy_param in zip(
            self.target_net.parameters(), self.policy_net.parameters()
        ):
            target_param.data.copy_(
                self.config.tau * policy_param.data +
                (1.0 - self.config.tau) * target_param.data
            )
    
    def _compute_curiosity(
        self,
        states: torch.Tensor,
        next_states: torch.Tensor,
    ) -> torch.Tensor:
        """Compute curiosity-driven intrinsic reward"""
        
        # Simple prediction error as curiosity
        if self.curiosity_net is None:
            self.curiosity_net = nn.Sequential(
                nn.Linear(self.config.state_size, 64),
                nn.ReLU(),
                nn.Linear(64, self.config.state_size),
            ).to(self.device)
            self.curiosity_optimizer = optim.Adam(
                self.curiosity_net.parameters(), lr=1e-4
            )
        
        # Predict next state
        predicted_next = self.curiosity_net(states)
        
        # Prediction error = curiosity
        curiosity = ((predicted_next - next_states) ** 2).mean()
        
        # Train curiosity network
        self.curiosity_optimizer.zero_grad()
        curiosity.backward(retain_graph=True)
        self.curiosity_optimizer.step()
        
        return curiosity.detach()
    
    def end_episode(self, total_reward: float) -> None:
        """End of episode processing"""
        self._total_episodes += 1
        self._total_rewards.append(total_reward)
        
        if total_reward > self._best_reward:
            self._best_reward = total_reward
            self.save(self.config.model_path)
        
        self._log.info(
            f"Episode {self._total_episodes}: reward={total_reward:.2f}, "
            f"epsilon={self.epsilon:.4f}, avg_loss={self._avg_loss:.4f}"
        )
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MODEL MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def save(self, path: str) -> None:
        """Save model checkpoint"""
        checkpoint = {
            'policy_net': self.policy_net.state_dict(),
            'target_net': self.target_net.state_dict(),
            'optimizer': self.optimizer.state_dict(),
            'epsilon': self.epsilon,
            'total_steps': self.total_steps,
            'config': self.config,
        }
        torch.save(checkpoint, path)
        self._log.info(f"Model saved: {path}")
    
    def load(self, path: str) -> bool:
        """Load model checkpoint"""
        try:
            checkpoint = torch.load(path, map_location=self.device)
            
            self._initialize_networks()
            self.policy_net.load_state_dict(checkpoint['policy_net'])
            self.target_net.load_state_dict(checkpoint['target_net'])
            self.optimizer.load_state_dict(checkpoint['optimizer'])
            self.epsilon = checkpoint.get('epsilon', self.config.epsilon_start)
            self.total_steps = checkpoint.get('total_steps', 0)
            
            self._log.info(f"Model loaded from {path}")
            return True
            
        except Exception as e:
            self._log.error(f"Failed to load model: {e}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """Get RL agent statistics"""
        recent_rewards = self._total_rewards[-10:] if self._total_rewards else [0]
        
        return {
            "episodes": self._total_episodes,
            "total_steps": self.total_steps,
            "epsilon": round(self.epsilon, 4),
            "avg_loss": round(self._avg_loss, 4),
            "avg_reward": round(np.mean(recent_rewards), 2),
            "best_reward": round(self._best_reward, 2),
            "replay_buffer_size": len(self.replay_buffer),
            "action_space": [a.value for a in self.config.action_space],
        }