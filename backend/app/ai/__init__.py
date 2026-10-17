# backend/app/ai/__init__.py
"""
AEGIS AI - zero-cost AI layer.

Default path is local/free ONLY (Ollama -> honest heuristic templates).
Paid providers (OpenAI/Gemini) exist but refuse to initialize unless the
operator explicitly sets ENABLE_PAID_LLM=true, and their SDKs are imported
lazily so this package never requires them.

Public surface:
    get_llm_provider()   -- factory; logs which provider is active
    reset_llm_provider() -- clear cached provider (tests)
    LLMProvider          -- generate(prompt, **kwargs) -> str interface
    OllamaProvider       -- DEFAULT; local Ollama, graceful when down
    HeuristicProvider    -- honest template fallback (source="heuristic-template")
    OpenAIProvider       -- OPTIONAL, paid, opt-in only
    GeminiProvider       -- OPTIONAL, paid, opt-in only
    ModelRegistry        -- plug-in point for the future self-trained model
    SelfTrainedModel     -- interface the future checkpoint wrapper implements
"""

from .base import GenerationInfo, LLMProvider, ProviderUnavailableError
from .factory import get_llm_provider, reset_llm_provider
from .heuristic import HeuristicProvider, pick_from_pools
from .model_registry import (
    ModelRegistry,
    RegisteredModel,
    RegisteredModelProvider,
    SelfTrainedModel,
)
from .ollama import OllamaProvider
from .optional_paid import GeminiProvider, OpenAIProvider

__all__ = [
    "GenerationInfo",
    "LLMProvider",
    "ProviderUnavailableError",
    "get_llm_provider",
    "reset_llm_provider",
    "HeuristicProvider",
    "pick_from_pools",
    "OllamaProvider",
    "OpenAIProvider",
    "GeminiProvider",
    "ModelRegistry",
    "RegisteredModel",
    "RegisteredModelProvider",
    "SelfTrainedModel",
]
