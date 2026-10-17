# backend/app/ai/optional_paid.py
"""
AEGIS AI - OPTIONAL paid providers (OpenAI / Gemini).

These are NEVER on the default path:

  1. They refuse to initialize unless the operator explicitly sets
     ENABLE_PAID_LLM=true in the environment. Anything else raises
     RuntimeError with a clear log message.
  2. Their third-party SDKs (openai, google-generativeai) are imported
     LAZILY inside methods, so `import backend.app.ai` never requires
     paid SDKs to be installed.

Default behaviour (flag unset): constructing either provider raises, the
factory logs the refusal, and the system falls back to Ollama -> heuristic
templates. Zero paid API spend unless the operator opts in.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from .base import LLMProvider, ProviderUnavailableError

log = logging.getLogger("aegis.ai.paid")


def _paid_llm_enabled() -> bool:
    return os.environ.get("ENABLE_PAID_LLM", "").strip().lower() == "true"


def _require_paid_enabled(provider_name: str) -> None:
    if not _paid_llm_enabled():
        msg = (
            f"{provider_name} is a PAID provider and is disabled by default. "
            "Set ENABLE_PAID_LLM=true to opt in explicitly (this will incur "
            "API charges). Falling back to local/free providers."
        )
        log.warning(msg)
        raise RuntimeError(msg)


class OpenAIProvider(LLMProvider):
    """Optional OpenAI provider. Disabled unless ENABLE_PAID_LLM=true."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        _require_paid_enabled("OpenAIProvider")
        super().__init__()
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        # Never log or store the key beyond this attribute; never print it.
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError(
                "OpenAIProvider: ENABLE_PAID_LLM=true but OPENAI_API_KEY is not set."
            )
        log.info("OpenAIProvider initialized (paid API enabled by operator).")

    @property
    def name(self) -> str:
        return "openai"

    def available(self) -> bool:
        if not _paid_llm_enabled() or not self._api_key:
            return False
        try:
            import openai  # lazy: SDK not required at import time
        except ImportError:
            return False
        return True

    def generate(self, prompt: str, **kwargs: Any) -> str:
        try:
            import openai  # lazy import
        except ImportError as e:
            raise ProviderUnavailableError(
                "openai SDK not installed. Run: pip install openai"
            ) from e
        start = time.time()
        try:
            client = openai.OpenAI(api_key=self._api_key)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=int(kwargs.get("max_tokens", 200)),
                temperature=float(kwargs.get("temperature", 0.8)),
            )
        except Exception as e:
            raise ProviderUnavailableError(f"OpenAI request failed: {e}") from e
        text = (resp.choices[0].message.content or "").strip()
        usage = getattr(resp, "usage", None)
        self._record(
            source=f"openai:{self.model}",
            model=self.model,
            latency_ms=(time.time() - start) * 1000.0,
            tokens_used=getattr(usage, "total_tokens", None),
        )
        return text

    @property
    def supports_translation(self) -> bool:
        return True


class GeminiProvider(LLMProvider):
    """Optional Google Gemini provider. Disabled unless ENABLE_PAID_LLM=true."""

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        _require_paid_enabled("GeminiProvider")
        super().__init__()
        self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not self._api_key:
            raise RuntimeError(
                "GeminiProvider: ENABLE_PAID_LLM=true but GEMINI_API_KEY is not set."
            )
        log.info("GeminiProvider initialized (paid API enabled by operator).")

    @property
    def name(self) -> str:
        return "gemini"

    def available(self) -> bool:
        if not _paid_llm_enabled() or not self._api_key:
            return False
        try:
            import google.generativeai  # lazy: SDK not required at import time
        except ImportError:
            return False
        return True

    def generate(self, prompt: str, **kwargs: Any) -> str:
        try:
            import google.generativeai as genai  # lazy import
        except ImportError as e:
            raise ProviderUnavailableError(
                "google-generativeai SDK not installed. "
                "Run: pip install google-generativeai"
            ) from e
        start = time.time()
        try:
            genai.configure(api_key=self._api_key)
            model = genai.GenerativeModel(self.model)
            resp = model.generate_content(
                prompt,
                generation_config={
                    "max_output_tokens": int(kwargs.get("max_tokens", 200)),
                    "temperature": float(kwargs.get("temperature", 0.8)),
                },
            )
        except Exception as e:
            raise ProviderUnavailableError(f"Gemini request failed: {e}") from e
        text = (getattr(resp, "text", "") or "").strip()
        self._record(
            source=f"gemini:{self.model}",
            model=self.model,
            latency_ms=(time.time() - start) * 1000.0,
        )
        return text

    @property
    def supports_translation(self) -> bool:
        return True
