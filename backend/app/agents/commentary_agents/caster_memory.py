# backend/app/agents/commentary_agents/caster_memory.py
"""
AEGIS AI - Caster Memory
Stores historical context for richer commentary.
Remembers past matches, player histories, tournament storylines.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import deque
import structlog

logger = structlog.get_logger("aegis.agents.caster_memory")

@dataclass
class PlayerMemory:
    """What the caster remembers about a player"""
    player_id: str
    player_name: str
    team_name: str
    total_kills: int = 0
    total_damage: int = 0
    clutch_count: int = 0
    mvp_count: int = 0
    notable_moments: deque = field(default_factory=lambda: deque(maxlen=10))
    last_match_kills: int = 0
    tournament_kills: int = 0
    playstyle: str = "unknown"
    signature_weapon: str = ""
    fun_facts: List[str] = field(default_factory=list)

@dataclass
class TeamMemory:
    """What the caster remembers about a team"""
    team_id: str
    team_name: str
    tournament_placement: int = 0
    total_wins: int = 0
    rivalry_teams: List[str] = field(default_factory=list)
    playstyle: str = "unknown"
    strength: str = ""
    weakness: str = ""
    recent_form: str = "unknown"  # hot, cold, stable
    fun_facts: List[str] = field(default_factory=list)

@dataclass
class TournamentStoryline:
    """Ongoing tournament narratives"""
    storyline_id: str
    title: str
    description: str
    teams_involved: List[str]
    players_involved: List[str]
    importance: float = 0.5  # 0-1
    is_active: bool = True

@dataclass
class HistoricalContext:
    """Complete historical context for a moment"""
    player_history: Dict[str, PlayerMemory] = field(default_factory=dict)
    team_history: Dict[str, TeamMemory] = field(default_factory=dict)
    storylines: List[TournamentStoryline] = field(default_factory=list)
    tournament_name: str = ""
    match_count: int = 0
    memorable_moments: deque = field(default_factory=lambda: deque(maxlen=50))


class CasterMemory:
    """
    Long-term memory for commentary context.
    
    Remembers:
    - Player histories (stats, playstyles, signature moves)
    - Team histories (rivalries, strengths, weaknesses)
    - Tournament storylines
    - Previous match outcomes
    - Head-to-head records
    - Fun facts and trivia
    
    Provides rich context so commentary feels knowledgeable
    and connected to the broader esports narrative.
    """
    
    def __init__(self):
        self.context = HistoricalContext()
        
        # Quick lookup
        self.player_memory: Dict[str, PlayerMemory] = {}
        self.team_memory: Dict[str, TeamMemory] = {}
        self.storylines: Dict[str, TournamentStoryline] = {}
        
        # Conversation memory (recently mentioned topics)
        self.recently_mentioned: deque = deque(maxlen=30)
        self.mention_cooldowns: Dict[str, float] = {}
        
        self._log = logger.bind(component="caster_memory")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MEMORY UPDATES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def remember_player(
        self,
        player_id: str,
        player_name: str,
        team_name: str,
        kills: int = 0,
        damage: int = 0,
        was_clutch: bool = False,
        was_mvp: bool = False,
        playstyle: str = "",
        weapon: str = "",
    ) -> None:
        """Update player memory"""
        
        if player_id not in self.player_memory:
            self.player_memory[player_id] = PlayerMemory(
                player_id=player_id,
                player_name=player_name,
                team_name=team_name,
            )
        
        memory = self.player_memory[player_id]
        memory.total_kills += kills
        memory.total_damage += damage
        memory.last_match_kills = kills
        
        if was_clutch:
            memory.clutch_count += 1
            memory.notable_moments.append({
                "type": "clutch",
                "timestamp": time.time(),
                "kills": kills,
            })
        
        if was_mvp:
            memory.mvp_count += 1
        
        if playstyle:
            memory.playstyle = playstyle
        
        if weapon and weapon not in [memory.signature_weapon]:
            if kills >= 3:  # Only remember signature weapons with good performance
                memory.signature_weapon = weapon
    
    def remember_team(
        self,
        team_id: str,
        team_name: str,
        placement: int = 0,
        was_win: bool = False,
        playstyle: str = "",
        rival: Optional[str] = None,
    ) -> None:
        """Update team memory"""
        
        if team_id not in self.team_memory:
            self.team_memory[team_id] = TeamMemory(
                team_id=team_id,
                team_name=team_name,
            )
        
        memory = self.team_memory[team_id]
        memory.tournament_placement = placement
        
        if was_win:
            memory.total_wins += 1
        
        if playstyle:
            memory.playstyle = playstyle
        
        if rival and rival not in memory.rivalry_teams:
            memory.rivalry_teams.append(rival)
        
        # Update form
        if placement <= 3:
            memory.recent_form = "hot"
        elif placement <= 8:
            memory.recent_form = "stable"
        else:
            memory.recent_form = "cold"
    
    def remember_storyline(self, storyline: TournamentStoryline) -> None:
        """Add a tournament storyline"""
        self.storylines[storyline.storyline_id] = storyline
        self.context.storylines.append(storyline)
        self._log.info(f"Storyline added: {storyline.title}")
    
    def remember_moment(self, description: str, importance: float) -> None:
        """Remember a notable moment"""
        self.context.memorable_moments.append({
            "description": description,
            "importance": importance,
            "timestamp": time.time(),
        })
    
    def mark_mentioned(self, topic: str) -> None:
        """Mark a topic as recently mentioned (to avoid repetition)"""
        self.recently_mentioned.append(topic)
        self.mention_cooldowns[topic] = time.time()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONTEXT RETRIEVAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_player_context(self, player_id: str) -> Optional[Dict[str, Any]]:
        """Get rich context about a player for commentary"""
        memory = self.player_memory.get(player_id)
        if not memory:
            return None
        
        # Build context
        context = {
            "name": memory.player_name,
            "team": memory.team_name,
            "stats": {
                "total_kills": memory.total_kills,
                "total_damage": memory.total_damage,
                "clutch_count": memory.clutch_count,
                "mvp_count": memory.mvp_count,
            },
            "playstyle": memory.playstyle,
            "signature_weapon": memory.signature_weapon,
        }
        
        # Add recent performance
        if memory.last_match_kills > 0:
            context["recent"] = f"{memory.last_match_kills} kills last match"
        
        # Add fun facts
        if memory.fun_facts:
            context["fun_fact"] = memory.fun_facts[-1]
        
        # Add notable moments
        if memory.notable_moments:
            context["notable"] = memory.notable_moments[-1]
        
        return context
    
    def get_team_context(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get rich context about a team for commentary"""
        memory = self.team_memory.get(team_id)
        if not memory:
            return None
        
        context = {
            "name": memory.team_name,
            "stats": {
                "placement": memory.tournament_placement,
                "wins": memory.total_wins,
            },
            "playstyle": memory.playstyle,
            "form": memory.recent_form,
        }
        
        # Add rivalries
        if memory.rivalry_teams:
            context["rivals"] = memory.rivalry_teams
        
        # Add strengths/weaknesses
        if memory.strength:
            context["strength"] = memory.strength
        if memory.weakness:
            context["weakness"] = memory.weakness
        
        return context
    
    def get_head_to_head_context(
        self,
        team_a_id: str,
        team_b_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Get head-to-head context between two teams"""
        team_a = self.team_memory.get(team_a_id)
        team_b = self.team_memory.get(team_b_id)
        
        if not team_a or not team_b:
            return None
        
        is_rivalry = team_b_id in team_a.rivalry_teams
        
        return {
            "team_a": team_a.team_name,
            "team_b": team_b.team_name,
            "is_rivalry": is_rivalry,
            "team_a_form": team_a.recent_form,
            "team_b_form": team_b.recent_form,
        }
    
    def get_active_storylines(
        self,
        teams: List[str],
        players: List[str],
    ) -> List[Dict[str, Any]]:
        """Get relevant storylines for current context"""
        relevant = []
        
        for storyline in self.storylines.values():
            if not storyline.is_active:
                continue
            
            # Check if teams or players are involved
            team_match = bool(set(storyline.teams_involved) & set(teams))
            player_match = bool(set(storyline.players_involved) & set(players))
            
            if team_match or player_match:
                relevant.append({
                    "title": storyline.title,
                    "description": storyline.description,
                    "importance": storyline.importance,
                })
        
        return sorted(relevant, key=lambda s: s["importance"], reverse=True)
    
    def get_recently_mentioned(self, limit: int = 5) -> List[str]:
        """Get recently mentioned topics"""
        return list(self.recently_mentioned)[-limit:]
    
    def should_mention(self, topic: str, cooldown: float = 30.0) -> bool:
        """Check if topic should be mentioned (not recently used)"""
        last_mentioned = self.mention_cooldowns.get(topic, 0)
        return (time.time() - last_mentioned) > cooldown
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # COMMENTARY ENRICHMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def enrich_commentary_context(self, base_context: Dict[str, Any]) -> Dict[str, Any]:
        """Enrich commentary context with historical data"""
        
        enriched = base_context.copy()
        
        # Add player context
        player_id = base_context.get("player_id")
        if player_id:
            player_ctx = self.get_player_context(player_id)
            if player_ctx:
                enriched["player_history"] = player_ctx
        
        # Add team context
        team_id = base_context.get("team_id")
        if team_id:
            team_ctx = self.get_team_context(team_id)
            if team_ctx:
                enriched["team_history"] = team_ctx
        
        # Add head-to-head
        team_a = base_context.get("team_a_id")
        team_b = base_context.get("team_b_id")
        if team_a and team_b:
            h2h = self.get_head_to_head_context(team_a, team_b)
            if h2h:
                enriched["head_to_head"] = h2h
        
        # Add storylines
        teams = [t for t in [team_id, team_a, team_b] if t]
        players = [p for p in [player_id] if p]
        storylines = self.get_active_storylines(teams, players)
        if storylines:
            enriched["storylines"] = storylines
        
        return enriched
    
    def get_fun_fact(self, context: Dict[str, Any]) -> Optional[str]:
        """Get a relevant fun fact for commentary"""
        
        # Check player facts
        player_id = context.get("player_id")
        if player_id and player_id in self.player_memory:
            memory = self.player_memory[player_id]
            if memory.fun_facts:
                return memory.fun_facts[-1]
            if memory.clutch_count >= 3:
                return f"{memory.player_name} is a known clutch player with {memory.clutch_count} clutch wins"
            if memory.signature_weapon:
                return f"{memory.player_name} is known for deadly {memory.signature_weapon} skills"
        
        # Check team facts
        team_id = context.get("team_id")
        if team_id and team_id in self.team_memory:
            memory = self.team_memory[team_id]
            if memory.rivalry_teams:
                return f"{memory.team_name} has a historic rivalry with {memory.rivalry_teams[0]}"
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get caster memory statistics"""
        return {
            "players_remembered": len(self.player_memory),
            "teams_remembered": len(self.team_memory),
            "active_storylines": len(self.storylines),
            "memorable_moments": len(self.context.memorable_moments),
            "match_count": self.context.match_count,
        }
    
    def reset_match(self) -> None:
        """Reset per-match memory (keep long-term)"""
        self.recently_mentioned.clear()
        self.mention_cooldowns.clear()
        self._log.info("Match memory reset")
    
    def reset(self) -> None:
        """Reset all memory"""
        self.player_memory.clear()
        self.team_memory.clear()
        self.storylines.clear()
        self.context = HistoricalContext()
        self.recently_mentioned.clear()
        self.mention_cooldowns.clear()
        self._log.info("Caster memory fully reset")