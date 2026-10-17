# backend/app/ai/heuristic.py
"""
AEGIS AI - Heuristic (template-based) provider.

This is NOT an AI model. It selects from human-written template pools and
must NEVER be presented as AI output. Every generation is labelled
source="heuristic-template" so downstream code (commentary, translation,
metadata) can stay honest.

It exists so the product keeps working with ZERO paid API spend and ZERO
local model installed: the broadcast still gets commentary lines, they are
just honestly template-generated.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, Dict, List, Optional

from .base import GenerationInfo, LLMProvider

# Generic, style-neutral fallback lines. Deliberately plain: they do not
# pretend to be clever, they just keep the broadcast from going silent.
_GENERIC_LINES: List[str] = [
    "And the action continues here.",
    "Let's see how this play develops.",
    "Both sides are feeling each other out at the moment.",
    "Patience is the name of the game right now.",
    "The tension is building as the next phase approaches.",
]


class HeuristicProvider(LLMProvider):
    """
    Honest template-based fallback provider.

    Args:
        template_fn: optional callable ``(prompt, **kwargs) -> str`` that
            picks a template line using the caller's own pools (e.g. the
            commentary ScriptGenerator's style pools). When omitted, a
            small generic pool is used.
        seed: optional random seed for deterministic output (tests).
    """

    def __init__(
        self,
        template_fn: Optional[Callable[..., str]] = None,
        seed: Optional[int] = None,
    ) -> None:
        super().__init__()
        self._template_fn = template_fn
        self._rng = random.Random(seed)

    @property
    def name(self) -> str:
        return "heuristic-template"

    def available(self) -> bool:
        # Templates need nothing: no network, no model, no key.
        return True

    @property
    def supports_translation(self) -> bool:
        # CRITICAL: templates must never be presented as translations.
        return False

    def generate(self, prompt: str, **kwargs: Any) -> str:
        start = time.time()
        if self._template_fn is not None:
            text = self._template_fn(prompt, **kwargs)
        else:
            text = self._rng.choice(_GENERIC_LINES)
        text = (text or "").strip() or self._rng.choice(_GENERIC_LINES)
        self._record(
            source="heuristic-template",
            model="template-pools",
            latency_ms=(time.time() - start) * 1000.0,
            extra={"prompt_chars": len(prompt)},
        )
        return text

    def last_info(self) -> GenerationInfo:
        """Convenience accessor; source is always 'heuristic-template'."""
        assert self.last_generation is not None
        assert self.last_generation.source == "heuristic-template"
        return self.last_generation


def pick_from_pools(
    pools: Dict[str, List[str]],
    style_key: str,
    rng: Optional[random.Random] = None,
) -> str:
    """
    Small helper callers can pass as ``template_fn``: pick a random line
    from ``pools[style_key]`` (falling back to any pool).
    """
    rng = rng or random
    candidates = pools.get(style_key) or []
    if not candidates:
        for lines in pools.values():
            candidates.extend(lines)
    if not candidates:
        candidates = _GENERIC_LINES
    return rng.choice(candidates)
