import json
import structlog
from typing import List
import redis

logger = structlog.get_logger("aegis.intelligence.points")

class PointsEngine:
    """
    AEGIS AI - Real-time Points Aggregator
    Fixes Issue #3: Calculates points math live and updates standings.
    Fixes Issue #1: Uses Redis for Zero Data-Loss State Persistence.
    """
    def __init__(self):
        # Fallback to in-memory dict if Redis is not installed
        self.use_redis = False
        self.memory_state = {}
        try:
            self.r = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
            self.r.ping()
            self.use_redis = True
            logger.info("Connected to Redis! Zero Data-Loss State Persistence Active.")
        except redis.ConnectionError:
            logger.warning("Redis not found. Falling back to RAM. (Install Redis to fix Issue #1)")

    def _get_team(self, team_id: str) -> dict:
        if self.use_redis:
            data = self.r.hget("aegis:standings", team_id)
            return json.loads(data) if data else {"id": team_id, "name": team_id, "finishes": 0, "alive": 4}
        else:
            return self.memory_state.get(team_id, {"id": team_id, "name": team_id, "finishes": 0, "alive": 4})

    def _save_team(self, team_id: str, data: dict):
        if self.use_redis:
            self.r.hset("aegis:standings", team_id, json.dumps(data))
        else:
            self.memory_state[team_id] = data

    def process_kill_event(self, killer_team: str, victim_team: str):
        """Math logic: Killer gets +1 point, Victim loses 1 alive player."""
        logger.info(f"Processing Live Math: {killer_team} kills {victim_team}")
        
        # Update Killer
        killer = self._get_team(killer_team)
        killer["finishes"] += 1
        self._save_team(killer_team, killer)
        
        # Update Victim
        victim = self._get_team(victim_team)
        if victim["alive"] > 0:
            victim["alive"] -= 1
        self._save_team(victim_team, victim)
        
        return self.get_leaderboard()

    def get_leaderboard(self) -> List[dict]:
        """Sorts the current state and returns the Top 16."""
        teams = []
        if self.use_redis:
            all_teams = self.r.hgetall("aegis:standings")
            for t_data in all_teams.values():
                teams.append(json.loads(t_data))
        else:
            teams = list(self.memory_state.values())
            
        # Sort by finishes (highest first), then by alive players
        teams.sort(key=lambda x: (x["finishes"], x["alive"]), reverse=True)
        return teams[:16]

# ── Usage Example ────────────────────────────────────────────────────────────
# Dev-only demo (never executed in production — guarded by __name__).
# The team tags below are ILLUSTRATIVE placeholders; the live engine only
# ever sees team IDs from real kill events via process_kill_event().
if __name__ == "__main__":
    engine = PointsEngine()
    engine.process_kill_event("GodL", "Soul")
    engine.process_kill_event("GodL", "Soul")
    engine.process_kill_event("TX", "GodL")
    print("LIVE LEADERBOARD:", json.dumps(engine.get_leaderboard(), indent=2))
