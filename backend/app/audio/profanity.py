"""AEGIS AI — commentary profanity filter (Phase 22 / M13).

A small, curated regex/wordlist pass over generated commentary text
BEFORE TTS synthesis or on-screen display. Covers common English, Hindi
(Devanagari) and Hinglish (Roman-script) profanity. Matched words are
replaced with ``***``; the event is logged and counted. The filter never
raises: any internal error returns the original text unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List

import structlog

logger = structlog.get_logger("aegis.audio.profanity")

# Curated list — common broadcast-unsafe terms only (kept deliberately
# small to avoid false positives on normal esports vocabulary).
_ENGLISH = [
    "fuck", "fucking", "shit", "bitch", "bastard", "asshole", "dickhead",
    "motherfucker", "whore", "slut", "cunt", "piss off",
]
_HINGLISH = [
    "bhenchod", "behenchod", "madarchod", "bhosdike", "bhosdi", "chutiya",
    "chutiye", "gandu", "harami", "kamina", "kamine", "saala sala",
    "randi", "laude", "lavde", "jhantu", "bakchod",
]
_HINDI_DEVANAGARI = [
    "बहनचोद", "मादरचोद", "चूतिया", "गांडू", "हरामी", "कमीना", "रंडी", "भोसडी",
]

_ALL_TERMS = _ENGLISH + _HINGLISH + _HINDI_DEVANAGARI


def _build_pattern() -> re.Pattern:
    parts = [re.escape(t) for t in sorted(_ALL_TERMS, key=len, reverse=True)]
    # Word-ish boundaries that also work around Devanagari (no \b there,
    # so use lookarounds on letters/digits/marks being absent).
    return re.compile(r"(?<!\w)(" + "|".join(parts) + r")(?!\w)", re.IGNORECASE)


_PATTERN = _build_pattern()

_filter_hits: int = 0


@dataclass
class FilterResult:
    text: str
    filtered: bool = False
    matches: List[str] = field(default_factory=list)


def filter_profanity(text: str) -> FilterResult:
    """Return text with profanity masked. Never raises."""
    global _filter_hits
    try:
        if not text:
            return FilterResult(text=text or "")
        found: List[str] = []

        def _mask(m: re.Match) -> str:
            found.append(m.group(0))
            return "***"

        cleaned = _PATTERN.sub(_mask, text)
        if found:
            _filter_hits += len(found)
            logger.warning(
                "profanity filtered from commentary",
                matches=found,
                total_hits=_filter_hits,
            )
            return FilterResult(text=cleaned, filtered=True, matches=found)
        return FilterResult(text=text)
    except Exception as exc:  # never crash the commentary path
        logger.error("profanity filter failed; passing text through", error=str(exc))
        return FilterResult(text=text or "")


def profanity_stats() -> dict:
    return {"total_filtered_terms": _filter_hits, "wordlist_size": len(_ALL_TERMS)}
