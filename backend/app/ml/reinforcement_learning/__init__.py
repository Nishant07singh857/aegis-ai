# backend/app/ml/reinforcement_learning/__init__.py
"""
AEGIS AI - Reinforcement Learning Module
RL for autonomous camera switching, broadcast direction,
and observer behavior optimization using Gym environments.
"""

from .observer_rl import ObserverRL, RLObserverConfig, CameraAction
from .environment import BroadcastEnvironment, EnvConfig, GameState
from .reward_function import RewardFunction, RewardConfig, RewardComponents

__all__ = [
    "ObserverRL",
    "RLObserverConfig", 
    "CameraAction",
    "BroadcastEnvironment",
    "EnvConfig",
    "GameState",
    "RewardFunction",
    "RewardConfig",
    "RewardComponents",
]