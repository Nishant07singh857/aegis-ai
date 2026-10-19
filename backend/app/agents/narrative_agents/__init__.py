# backend/app/agents/narrative_agents/__init__.py
"""
AEGIS AI - Narrative Agents
Storyline building, rivalry tracking, comeback detection,
and automatic documentary generation for esports narratives.
"""

from .storyline_builder import StorylineBuilder, Storyline, StoryArc
from .rivalry_tracker import RivalryTracker, Rivalry, RivalryLevel
from .comeback_detector import ComebackDetector, ComebackStory
from .documentary_generator import DocumentaryGenerator, DocumentaryScript
from .storyteller import StorytellerAgent, StoryType, StoryDataProvider

__all__ = [
    "StorylineBuilder",
    "Storyline",
    "StoryArc",
    "RivalryTracker",
    "Rivalry",
    "RivalryLevel",
    "ComebackDetector",
    "ComebackStory",
    "DocumentaryGenerator",
    "DocumentaryScript",
    "StorytellerAgent",
    "StoryType",
    "StoryDataProvider",
]