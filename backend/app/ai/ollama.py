# backend/app/ai/ollama.py
"""
AEGIS AI - Ollama provider (DEFAULT, local and free).

Talks to a local Ollama server (default http://localhost:11434) using only
the Python standard library (urllib) -- no extra dependencies.

Graceful degradation is the whole point of this file:
  - If Ollama is not installed / not running, available() returns False
    (it does NOT raise) and generate() raises ProviderUnavailableError,
    so callers fall through to the HeuristicProvider.
  - Nothing here ever touches a paid API.

To use a real local model:
    1. Install Ollama: https://ollama.com
    2. Pull a model:   ollama pull llama3.1
    3. Run:            ollama serve   (or just `ollama run llama3.1`)
    4. Optionally set OLLAMA_MODEL / OLLAMA_HOST env vars.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
import urllib.error
from typing import Any, Optional

from .base import LLMProvider, ProviderUnavailableError

log = logging.getLogger("aegis.ai.ollama")

DEFAULT_HOST = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1"

# Cache availability probes briefly so a down server doesn't add a
# 2-second penalty to every single commentary line.
_AVAILABILITY_TTL_S = 30.0


class OllamaProvider(LLMProvider):
    """Local Ollama LLM. Free, private, no API key, no cloud."""

    def __init__(
        self,
        host: Optional[str] = None,
        model: Optional[str] = None,
        request_timeout_s: float = 30.0,
        probe_timeout_s: float = 2.0,
    ) -> None:
        super().__init__()
        self.host = (host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL)
        self.request_timeout_s = request_timeout_s
        self.probe_timeout_s = probe_timeout_s
        self._available_cache: Optional[bool] = None
        self._available_checked_at: float = 0.0

    @property
    def name(self) -> str:
        return "ollama"

    # -- availability -------------------------------------------------
    def available(self) -> bool:
        """
        Cheap probe: GET /api/tags. Returns False (never raises) when
        Ollama is not reachable. Result cached for _AVAILABILITY_TTL_S.
        """
        now = time.time()
        if (
            self._available_cache is not None
            and (now - self._available_checked_at) < _AVAILABILITY_TTL_S
        ):
            return self._available_cache
        try:
            req = urllib.request.Request(f"{self.host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=self.probe_timeout_s) as resp:
                ok = 200 <= resp.status < 300
        except Exception:
            # Connection refused / timeout / DNS: Ollama simply isn't there.
            ok = False
        self._available_cache = ok
        self._available_checked_at = now
        return ok

    def invalidate_availability_cache(self) -> None:
        self._available_cache = None

    # -- generation ---------------------------------------------------
    def generate(self, prompt: str, **kwargs: Any) -> str:
        system = kwargs.get("system")
        max_tokens = int(kwargs.get("max_tokens", 200))
        temperature = float(kwargs.get("temperature", 0.8))

        payload: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": max_tokens,
                "temperature": temperature,
            },
        }
        if system:
            payload["system"] = system

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.host}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=self.request_timeout_s) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Model not pulled, bad request, etc. Invalidate probe cache:
            # the server IS up, so available() stays True but this call failed.
            raise ProviderUnavailableError(
                f"Ollama request failed (HTTP {e.code}): {e.reason}. "
                f"Is model '{self.model}' pulled? Run: ollama pull {self.model}"
            ) from e
        except Exception as e:
            self.invalidate_availability_cache()
            raise ProviderUnavailableError(
                f"Ollama not reachable at {self.host}: {e}"
            ) from e

        text = (body.get("response") or "").strip()
        latency_ms = (time.time() - start) * 1000.0
        # Ollama /api/generate (non-streaming) does not return token counts
        # in all versions; report honestly instead of inventing numbers.
        self._record(
            source=f"ollama:{self.model}",
            model=self.model,
            latency_ms=latency_ms,
            tokens_used=body.get("eval_count"),
            extra={"host": self.host, "done_reason": body.get("done_reason")},
        )
        if not text:
            raise ProviderUnavailableError(
                f"Ollama returned an empty response for model '{self.model}'."
            )
        return text

    @property
    def supports_translation(self) -> bool:
        # A real local LLM can translate; templates cannot.
        return True
