# backend/app/ai/factory.py
"""
AEGIS AI - provider factory.

Selection order (env AEGIS_LLM_PROVIDER, default "ollama"):

  1. "ollama"     -> OllamaProvider if reachable, else HeuristicProvider
  2. "heuristic"  -> HeuristicProvider (always works, honestly labelled)
  3. "openai"     -> OpenAIProvider (requires ENABLE_PAID_LLM=true),
                     else refuse + fall back to heuristic
  4. "gemini"     -> GeminiProvider (requires ENABLE_PAID_LLM=true),
                     else refuse + fall back to heuristic
  5. anything else -> warn + HeuristicProvider

The active provider is chosen ONCE (singleton) and logged at INFO so
operators can always see what is actually generating text. Call
reset_llm_provider() in tests to re-evaluate the environment.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .base import LLMProvider
from .heuristic import HeuristicProvider
from .ollama import OllamaProvider

log = logging.getLogger("aegis.ai.factory")

_ENV_VAR = "AEGIS_LLM_PROVIDER"

_singleton: Optional[LLMProvider] = None


def _build(requested: str) -> LLMProvider:
    requested = (requested or "ollama").strip().lower()

    if requested == "heuristic":
        log.info("AI provider active: heuristic-template (honest template fallback)")
        return HeuristicProvider()

    if requested in ("openai", "gemini"):
        # Local import: optional_paid must never be imported unless asked for,
        # so a default install never even loads the paid-provider code.
        from .optional_paid import GeminiProvider, OpenAIProvider

        try:
            provider = OpenAIProvider() if requested == "openai" else GeminiProvider()
        except RuntimeError as e:
            # Refused: ENABLE_PAID_LLM not set (or key missing). The
            # RuntimeError message was already logged as a warning.
            log.warning("Paid provider refused (%s); using heuristic-template.", e)
            return HeuristicProvider()
        if provider.available():
            log.info("AI provider active: %s (PAID - enabled by operator)", provider.name)
            return provider
        log.warning(
            "Paid provider '%s' unavailable (SDK missing?); using heuristic-template.",
            provider.name,
        )
        return HeuristicProvider()

    if requested == "ollama":
        provider = OllamaProvider()
        if provider.available():
            log.info(
                "AI provider active: ollama (model=%s, host=%s)",
                provider.model,
                provider.host,
            )
            return provider
        log.info(
            "Ollama not reachable at %s - falling back to heuristic-template "
            "(no LLM installed; commentary will use honest templates). "
            "To enable local AI: install Ollama and run 'ollama pull %s'.",
            provider.host,
            provider.model,
        )
        return HeuristicProvider()

    log.warning(
        "Unknown %s=%r; expected ollama|heuristic|openai|gemini. "
        "Using heuristic-template.",
        _ENV_VAR,
        requested,
    )
    return HeuristicProvider()


def get_llm_provider(force: Optional[str] = None) -> LLMProvider:
    """
    Return the active LLM provider (singleton).

    Args:
        force: bypass the singleton and build for this explicit selection.
               Useful for per-call overrides; does not change the cached one.
    """
    global _singleton
    if force is not None:
        return _build(force)
    if _singleton is None:
        _singleton = _build(os.environ.get(_ENV_VAR, "ollama"))
    return _singleton


def reset_llm_provider() -> None:
    """Clear the cached provider so the next call re-reads the environment."""
    global _singleton
    _singleton = None
