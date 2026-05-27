# backend/app/agents/commentary_agents/__init__.py
"""
AEGIS AI - Commentary Agents
Complete AI commentary system with script generation,
emotion engine, hype detection, voice synthesis,
caster memory, and multi-language support.
"""

from .script_generator import ScriptGenerator, CommentaryStyle, CommentaryTone
from .emotion_engine import EmotionEngine, EmotionalState
from .hype_detector import HypeDetector, HypeMoment
from .voice_synthesizer import VoiceSynthesizer, VoiceProfile
from .caster_memory import CasterMemory, HistoricalContext
from .language_manager import LanguageManager, Language

__all__ = [
    "ScriptGenerator",
    "CommentaryStyle",
    "CommentaryTone",
    "EmotionEngine",
    "EmotionalState",
    "HypeDetector",
    "HypeMoment",
    "VoiceSynthesizer",
    "VoiceProfile",
    "CasterMemory",
    "HistoricalContext",
    "LanguageManager",
    "Language",
]