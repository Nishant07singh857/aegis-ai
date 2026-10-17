# backend/app/agents/agent_registry.py
"""
AEGIS AI - Agent Registry
Central registry for agent discovery, tracking, and management.
Maintains references to all active agents in the system.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from collections import defaultdict
import structlog

from .base_agent import BaseAgent, AgentState, AgentPriority
from ..core.event_bus import EventBus

logger = structlog.get_logger("aegis.agents.registry")


@dataclass
class AgentRecord:
    """Record of a registered agent"""
    agent_id: str
    agent_type: str
    agent_instance: BaseAgent
    priority: AgentPriority
    registered_at: float = field(default_factory=time.time)
    last_heartbeat: float = 0.0
    is_active: bool = False
    
    def get_summary(self) -> Dict[str, Any]:
        """Get agent record summary"""
        return {
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "priority": self.priority.name,
            "is_active": self.is_active,
            "uptime": round(time.time() - self.registered_at, 1),
            "last_heartbeat_ago": round(time.time() - self.last_heartbeat, 1),
        }


class AgentRegistry:
    """
    Central registry for all AI agents in the system.
    
    Responsibilities:
    - Register and track all agent instances
    - Discover agents by type, priority, or status
    - Monitor agent health
    - Coordinate agent lifecycle
    - Provide agent lookup for inter-agent communication
    """
    
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.agents: Dict[str, AgentRecord] = {}
        
        # Type-based indexing
        self.agents_by_type: Dict[str, List[str]] = defaultdict(list)
        self.agents_by_priority: Dict[AgentPriority, List[str]] = defaultdict(list)
        self.agents_by_state: Dict[AgentState, List[str]] = defaultdict(list)
        
        # Statistics
        self._total_registered = 0
        self._total_started = 0
        self._total_stopped = 0
        self._total_errors = 0
        
        # Subscribe to heartbeats
        self.event_bus.subscribe(
            subscriber_id="agent_registry_heartbeat",
            callback=self._on_agent_heartbeat,
            event_types=["agent_heartbeat"],
        )
        
        self._log = logger.bind(component="agent_registry")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REGISTRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def register(
        self,
        agent_id: str,
        agent_type: str,
        agent_instance: BaseAgent,
        priority: AgentPriority = AgentPriority.MEDIUM,
    ) -> AgentRecord:
        """
        Register an agent in the registry.
        
        Args:
            agent_id: Unique agent identifier
            agent_type: Type of agent (team_agent, fight_detector, etc.)
            agent_instance: The agent instance
            priority: Agent priority level
            
        Returns:
            AgentRecord for the registered agent
        """
        # Check for duplicate
        if agent_id in self.agents:
            self._log.warning(f"Agent already registered: {agent_id}")
            return self.agents[agent_id]
        
        # Create record
        record = AgentRecord(
            agent_id=agent_id,
            agent_type=agent_type,
            agent_instance=agent_instance,
            priority=priority,
        )
        
        # Store in main registry
        self.agents[agent_id] = record
        
        # Index by type
        self.agents_by_type[agent_type].append(agent_id)
        
        # Index by priority
        self.agents_by_priority[priority].append(agent_id)
        
        self._total_registered += 1
        
        self._log.info(
            f"Agent registered: {agent_id}",
            type=agent_type,
            priority=priority.name,
            total=self._total_registered,
        )
        
        return record
    
    def unregister(self, agent_id: str) -> bool:
        """
        Unregister an agent from the registry.
        
        Args:
            agent_id: Agent to remove
            
        Returns:
            True if removed, False if not found
        """
        record = self.agents.pop(agent_id, None)
        if not record:
            return False
        
        # Remove from indexes
        self.agents_by_type[record.agent_type].remove(agent_id)
        self.agents_by_priority[record.priority].remove(agent_id)
        
        # Remove from state index (avoid stale entries)
        for state_list in self.agents_by_state.values():
            if agent_id in state_list:
                state_list.remove(agent_id)
        
        # Clean up empty lists
        if not self.agents_by_type[record.agent_type]:
            del self.agents_by_type[record.agent_type]
        if not self.agents_by_priority[record.priority]:
            del self.agents_by_priority[record.priority]
        
        self._log.info(f"Agent unregistered: {agent_id}")
        
        return True
    
    def mark_active(self, agent_id: str) -> None:
        """Mark an agent as active"""
        record = self.agents.get(agent_id)
        if record:
            record.is_active = True
            self._total_started += 1
    
    def mark_inactive(self, agent_id: str) -> None:
        """Mark an agent as inactive"""
        record = self.agents.get(agent_id)
        if record:
            record.is_active = False
            self._total_stopped += 1
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEARTBEAT HANDLING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _on_agent_heartbeat(self, record: Any) -> None:
        """Handle agent heartbeat events"""
        data = record.event.data if hasattr(record, 'event') else record.get("data", {})
        agent_id = data.get("agent_id", "")
        
        agent_record = self.agents.get(agent_id)
        if agent_record:
            agent_record.last_heartbeat = time.time()
            
            # Update state index
            agent_state = data.get("state", "running")
            state_enum = AgentState(agent_state) if agent_state in [s.value for s in AgentState] else AgentState.RUNNING
            
            # Remove from old state
            for state_list in self.agents_by_state.values():
                if agent_id in state_list:
                    state_list.remove(agent_id)
            
            # Add to new state
            self.agents_by_state[state_enum].append(agent_id)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DISCOVERY & LOOKUP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_agent(self, agent_id: str) -> Optional[BaseAgent]:
        """Get agent instance by ID"""
        record = self.agents.get(agent_id)
        return record.agent_instance if record else None
    
    def get_agent_record(self, agent_id: str) -> Optional[AgentRecord]:
        """Get agent record by ID"""
        return self.agents.get(agent_id)
    
    def get_agents_by_type(self, agent_type: str) -> List[BaseAgent]:
        """Get all agents of a specific type"""
        agent_ids = self.agents_by_type.get(agent_type, [])
        return [
            self.agents[aid].agent_instance
            for aid in agent_ids
            if aid in self.agents
        ]
    
    def get_agents_by_priority(self, priority: AgentPriority) -> List[BaseAgent]:
        """Get all agents at a priority level"""
        agent_ids = self.agents_by_priority.get(priority, [])
        return [
            self.agents[aid].agent_instance
            for aid in agent_ids
            if aid in self.agents
        ]
    
    def get_active_agents(self) -> List[BaseAgent]:
        """Get all currently active agents"""
        return [
            record.agent_instance
            for record in self.agents.values()
            if record.is_active
        ]
    
    def get_agent_ids_by_type(self, agent_type: str) -> List[str]:
        """Get agent IDs for a specific type"""
        return self.agents_by_type.get(agent_type, [])
    
    def find_agents(
        self,
        agent_type: Optional[str] = None,
        priority: Optional[AgentPriority] = None,
        is_active: Optional[bool] = None,
    ) -> List[BaseAgent]:
        """
        Find agents matching criteria.
        
        Args:
            agent_type: Filter by agent type
            priority: Filter by priority
            is_active: Filter by active status
            
        Returns:
            List of matching agent instances
        """
        results = []
        
        for record in self.agents.values():
            if agent_type and record.agent_type != agent_type:
                continue
            if priority and record.priority != priority:
                continue
            if is_active is not None and record.is_active != is_active:
                continue
            
            results.append(record.agent_instance)
        
        return results
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BULK OPERATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start_all_by_priority(self, priority: AgentPriority) -> int:
        """Start all agents at a priority level"""
        agents = self.get_agents_by_priority(priority)
        started = 0
        
        for agent in agents:
            try:
                await agent.start()
                self.mark_active(agent.config.agent_id)
                started += 1
            except Exception as e:
                self._log.error(f"Failed to start agent {agent.config.agent_id}: {e}")
                self._total_errors += 1
        
        return started
    
    async def stop_all_by_type(self, agent_type: str) -> int:
        """Stop all agents of a specific type"""
        agents = self.get_agents_by_type(agent_type)
        stopped = 0
        
        for agent in agents:
            try:
                await agent.stop()
                self.mark_inactive(agent.config.agent_id)
                stopped += 1
            except Exception as e:
                self._log.error(f"Failed to stop agent {agent.config.agent_id}: {e}")
                self._total_errors += 1
        
        return stopped
    
    async def stop_all(self) -> int:
        """Stop all registered agents"""
        stopped = 0
        
        for agent_id, record in list(self.agents.items()):
            try:
                await record.agent_instance.stop()
                self.mark_inactive(agent_id)
                stopped += 1
            except Exception as e:
                self._log.error(f"Failed to stop agent {agent_id}: {e}")
                self._total_errors += 1
        
        return stopped
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # HEALTH MONITORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_unhealthy_agents(self, max_heartbeat_age: float = 10.0) -> List[str]:
        """Get agents that haven't sent recent heartbeats"""
        unhealthy = []
        current_time = time.time()
        
        for agent_id, record in self.agents.items():
            if record.is_active:
                heartbeat_age = current_time - record.last_heartbeat
                if heartbeat_age > max_heartbeat_age:
                    unhealthy.append(agent_id)
        
        return unhealthy
    
    def get_agents_needing_restart(self) -> List[str]:
        """Get agents that may need restart based on error count"""
        needs_restart = []
        
        for agent_id, record in self.agents.items():
            agent = record.agent_instance
            if agent.state == AgentState.ERROR:
                needs_restart.append(agent_id)
        
        return needs_restart
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS & REPORTING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_summary(self) -> Dict[str, Any]:
        """Get comprehensive registry summary"""
        agent_summaries = {}
        
        for agent_type in self.agents_by_type:
            agents = self.get_agents_by_type(agent_type)
            active = sum(1 for a in agents if a.state == AgentState.RUNNING)
            
            agent_summaries[agent_type] = {
                "total": len(agents),
                "active": active,
                "inactive": len(agents) - active,
                "healthy": len(agents) - len([
                    a for a in agents if a.state == AgentState.ERROR
                ]),
            }
        
        return {
            "overview": {
                "total_registered": self._total_registered,
                "active_agents": len(self.get_active_agents()),
                "total_started": self._total_started,
                "total_stopped": self._total_stopped,
                "total_errors": self._total_errors,
            },
            "by_type": agent_summaries,
            "unhealthy_agents": self.get_unhealthy_agents(),
            "agents_needing_restart": self.get_agents_needing_restart(),
        }
    
    def get_agent_details(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a specific agent"""
        record = self.agents.get(agent_id)
        if not record:
            return None
        
        agent = record.agent_instance
        return {
            **record.get_summary(),
            "status": agent.get_status(),
            "health": agent.get_health_report(),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics"""
        return self.get_summary()
    
    def reset(self) -> None:
        """Reset registry (for testing/new tournament)"""
        self.agents.clear()
        self.agents_by_type.clear()
        self.agents_by_priority.clear()
        self.agents_by_state.clear()
        self._total_registered = 0
        self._total_started = 0
        self._total_stopped = 0
        self._total_errors = 0
        self._log.info("Agent registry reset")