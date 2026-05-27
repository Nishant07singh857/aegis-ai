# backend/app/agents/__init__.py
"""
AEGIS AI - Multi-Agent System
16 Team Agents + Fight Detection + Inter-Agent Communication
The intelligent core of the esports operating system
"""

from .base_agent import BaseAgent, AgentState, AgentConfig, AgentMetrics
from .orchestrator import AgentOrchestrator

__all__ = [
    "BaseAgent",
    "AgentState", 
    "AgentConfig",
    "AgentMetrics",
    "AgentOrchestrator",
]