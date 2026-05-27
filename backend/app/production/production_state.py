from dataclasses import dataclass
import time

@dataclass
class ProductionState:
    current_scene: str = "SAFE_SCENE_MAP"
    is_obs_connected: bool = False
    is_replay_active: bool = False
    last_switch_time: float = 0.0
    switches_in_last_minute: int = 0
    manual_override_active: bool = False
    
    def lock_for_replay(self, duration: float):
        self.is_replay_active = True
        self.replay_end_time = time.time() + duration
