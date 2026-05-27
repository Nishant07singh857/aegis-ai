# backend/app/ml/commentary/__init__.py
"""
AEGIS AI - Commentary ML Module
LLM-based commentary generation with prompt engineering,
context building, fine-tuning, and language-specific templates.
"""

from .llm_chain import LLMChain, ChainConfig, CommentaryPrompt
from .prompt_templates.few_shot_examples import FewShotExamples, ExampleCategory
from .prompt_templates.context_builder import ContextBuilder, CommentaryContext

__all__ = [
    "LLMChain",
    "ChainConfig",
    "CommentaryPrompt",
    "FewShotExamples",
    "ExampleCategory",
    "ContextBuilder",
    "CommentaryContext",
]