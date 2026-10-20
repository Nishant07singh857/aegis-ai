# backend/app/ml/commentary/__init__.py
"""
AEGIS AI - Commentary ML Module

Prompt engineering, context building and language-specific templates
for commentary generation.

Phase 22 (W3, hygiene): ``llm_chain.py`` was DELETED. It was dead
code (zero production callers) carrying stale paid-provider defaults
(gpt-4 / Anthropic references) that contradicted the project's
zero-API-spend policy. The live commentary path is ``app.ai``
(Ollama + honest heuristic fallback) via
``agents/commentary_agents/script_generator.py``. Do not re-add a
second LLM chain here.
"""

from .prompt_templates.few_shot_examples import FewShotExamples, ExampleCategory
from .prompt_templates.context_builder import ContextBuilder, CommentaryContext

__all__ = [
    "FewShotExamples",
    "ExampleCategory",
    "ContextBuilder",
    "CommentaryContext",
]
