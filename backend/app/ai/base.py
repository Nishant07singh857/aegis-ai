# backend/app/ai/base.py
"""
AEGIS AI - LLM provider abstraction (zero-cost AI layer).

HARD RULE (user constraint): no paid API calls in default code paths.
Every provider here either runs locally/free (Ollama, heuristic templates)
or is an OPTIONAL paid provider that refuses to initialize unless the
operator explicitly sets ENABLE_PAID_LLM=true.

Honest-labelling rule: any output that is NOT produced by a real model
must carry source="heuristic-template" in its metadata, and must never
be presented as AI-generated.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class ProviderUnavailableError(RuntimeError):
    """Raised when a provider cannot serve a request (e.g. Ollama not running)."""


@dataclass
class GenerationInfo:
    """
    Honest metadata about the most recent generation.

    `source` is the ground truth of what produced the text:
      - "ollama:<model>"      -> local Ollama model (real LLM, free)
      - "openai:<model>"      -> paid OpenAI (only when ENABLE_PAID_LLM=true)
      - "gemini:<model>"      -> paid Gemini (only when ENABLE_PAID_LLM=true)
      - "heuristic-template"  -> NOT AI. Template/random-choice fallback.
      - "self-trained:<name>" -> the user's future self-trained checkpoint.
    """
    source: str = "unknown"
    model: str = ""
    latency_ms: float = 0.0
    tokens_used: Optional[int] = None
    generated_at: float = field(default_factory=time.time)
    extra: Dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """
    Interface every text-generation provider must implement.

    Implementations:
      - OllamaProvider      (default; local, free)
      - HeuristicProvider   (always-available honest template fallback)
      - OpenAIProvider      (optional; needs ENABLE_PAID_LLM=true)
      - GeminiProvider      (optional; needs ENABLE_PAID_LLM=true)
      - RegisteredModelProvider (wraps a future self-trained checkpoint)
    """

    def __init__(self) -> None:
        self.last_generation: Optional[GenerationInfo] = None

    @property
    @abstractmethod
    def name(self) -> str:
        """Short provider identifier, e.g. 'ollama', 'heuristic-template'."""
        ...

    @abstractmethod
    def available(self) -> bool:
        """
        Cheap, side-effect-free check: can this provider serve right now?
        Must NEVER raise for the common "not running / not configured" case;
        return False instead so callers can fall through gracefully.
        """
        ...

    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """
        Generate text for `prompt`. Synchronous (callers that are async
        should run this in a thread, e.g. asyncio.to_thread).

        Common kwargs: max_tokens (int), temperature (float), system (str).

        Raises:
            ProviderUnavailableError: provider looked available but the
                request failed (connection dropped, model missing, ...).
        """
        ...

    @property
    def supports_translation(self) -> bool:
        """
        Whether this provider can be trusted to translate text.
        The HeuristicProvider MUST return False: templates must never
        be presented as translations.
        """
        return False

    def _record(self, **kwargs: Any) -> None:
        """Store honest metadata about the last generation."""
        self.last_generation = GenerationInfo(**kwargs)

    def last_source(self) -> str:
        """Honest source label of the most recent generation."""
        if self.last_generation:
            return self.last_generation.source
        return "none"
