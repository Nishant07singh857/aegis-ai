# backend/app/agents/commentary_agents/language_manager.py
"""
AEGIS AI - Language Manager
Multi-language support for commentary.
Handles Hindi, Hinglish, English, and regional languages.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.language")

class Language(str, Enum):
    """Supported languages"""
    HINDI = "hi"
    ENGLISH = "en"
    HINGLISH = "hinglish"  # Mixed Hindi-English
    TAMIL = "ta"
    TELUGU = "te"
    BENGALI = "bn"
    MARATHI = "mr"
    PUNJABI = "pa"

class Formality(str, Enum):
    """Language formality level"""
    CASUAL = "casual"
    FORMAL = "formal"
    SLANG = "slang"
    ESPORTS = "esports"  # Gaming slang

@dataclass
class LanguageProfile:
    """Language configuration"""
    language: Language
    name: str
    is_active: bool = True
    formality: Formality = Formality.ESPORTS
    
    # Phrase templates
    templates: Dict[str, List[str]] = field(default_factory=dict)
    
    # Common phrases
    fillers: List[str] = field(default_factory=list)
    exclamations: List[str] = field(default_factory=list)
    transitions: List[str] = field(default_factory=list)

@dataclass
class TranslationCache:
    """Cache for translated phrases"""
    key: str
    translation: str
    language: Language
    cached_at: float = field(default_factory=time.time)


@dataclass
class TranslationResult:
    """
    Honest result of a translate() call.

    translated=False means NO translation happened: `text` is the original
    input unchanged (e.g. no capable provider available). Callers must not
    present such output as translated. `provider` names what did the work:
    "none", or the LLM provider label like "ollama:llama3.1".
    """
    text: str
    translated: bool
    source_language: str
    target_language: str
    provider: str = "none"
    note: str = ""


class LanguageManager(BaseAgent):
    """
    Manages multi-language commentary support.
    
    Features:
    - Multiple Indian languages
    - Hinglish (mixed Hindi-English) - most popular for BGMI
    - Language-specific phrase templates
    - Translation caching
    - Regional flavor and slang
    - Language switching during broadcast
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Language profiles
        self.profiles: Dict[Language, LanguageProfile] = {}
        
        # Active language
        self.active_language: Language = Language.HINGLISH
        self.secondary_language: Optional[Language] = Language.ENGLISH
        
        # Translation cache
        self.translation_cache: Dict[str, TranslationCache] = {}

        # Zero-cost AI layer (backend/app.ai). Resolved lazily on first
        # translate() call so importing this module has no side effects.
        self._llm_provider = None
        self._llm_provider_failed = False

        # Initialize profiles
        self._initialize_profiles()

        self._log = logger.bind(component="language_manager")
    
    def _initialize_profiles(self) -> None:
        """Initialize language profiles with phrases"""
        
        # Hinglish (primary)
        self.profiles[Language.HINGLISH] = LanguageProfile(
            language=Language.HINGLISH,
            name="Hinglish",
            formality=Formality.ESPORTS,
            templates={
                "kill": [
                    "BHAI! {player} ne {victim} ko {weapon} se eliminate kiya!",
                    "Kya shot tha! {player} from {team} with the {weapon} kill!",
                    "Arey! {player} ne perfect {weapon} spray se kill liya!",
                ],
                "multi_kill": [
                    "BHAI BHAI BHAI! {player} on FIRE! {count} kills in {time} seconds!",
                    "RAMPAGE! {player} destroying everyone! {count} kills!",
                ],
                "clutch": [
                    "ARE BHAI! {player} 1v{enemies} clutch situation! Can they do it?",
                    "Tension at its peak! {player} alone against {enemies} enemies!",
                ],
                "clutch_win": [
                    "LEGENDARY! {player} WON the 1v{enemies} clutch! What a player!",
                    "KAMAAL KAR DIYA! {player} ne impossible ko possible bana diya!",
                ],
                "team_wipe": [
                    "GAYE! {team} eliminated! {eliminator} ne squad wipe kar diya!",
                    "And {team} is OUT! Complete destruction by {eliminator}!",
                ],
            },
            fillers=[
                "Dekhte hain aage kya hota hai...",
                "Interesting situation building up...",
                "Let's see how this plays out...",
            ],
            exclamations=[
                "BHAI!", "AREY!", "WAH!", "KAMAAL!", "HAI NA!",
                "WHAT!", "OH MY GOD!", "LET'S GO!",
            ],
            transitions=[
                "Aur ab dekhte hain...",
                "Meanwhile...",
                "On the other side...",
            ],
        )
        
        # Hindi
        self.profiles[Language.HINDI] = LanguageProfile(
            language=Language.HINDI,
            name="Hindi",
            formality=Formality.CASUAL,
            templates={
                "kill": [
                    "{player} ने {victim} को {weapon} से हराया!",
                    "क्या निशाना मारा {player} ने! शानदार!",
                ],
                "clutch_win": [
                    "अविश्वसनीय! {player} ने 1v{enemies} clutch जीत लिया!",
                ],
            },
            fillers=["देखते हैं आगे क्या होता है..."],
            exclamations=["वाह!", "कमाल!", "अद्भुत!"],
            transitions=["और अब..."],
        )
        
        # English
        self.profiles[Language.ENGLISH] = LanguageProfile(
            language=Language.ENGLISH,
            name="English",
            formality=Formality.FORMAL,
            templates={
                "kill": [
                    "{player} eliminates {victim} with the {weapon}!",
                    "Brilliant {weapon} work from {player} of {team}!",
                ],
                "clutch_win": [
                    "UNBELIEVABLE! {player} wins the 1v{enemies} clutch!",
                ],
            },
            fillers=["Let's see how this develops..."],
            exclamations=["INCREDIBLE!", "AMAZING!", "BRILLIANT!"],
            transitions=["Meanwhile...", "On the other hand..."],
        )
        
        self._log.info(f"Initialized {len(self.profiles)} language profiles")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LANGUAGE OPERATIONS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_active_language(self, language: Language) -> bool:
        """Set active commentary language"""
        if language in self.profiles:
            self.active_language = language
            self._log.info(f"Active language: {language.value}")
            return True
        return False
    
    def get_phrase(
        self,
        template_key: str,
        context: Dict[str, str],
        language: Optional[Language] = None,
    ) -> str:
        """Get a localized phrase from template"""
        lang = language or self.active_language
        profile = self.profiles.get(lang)
        
        if not profile:
            profile = self.profiles[Language.HINGLISH]
        
        templates = profile.templates.get(template_key, [])
        
        if not templates:
            # Fallback to English
            en_profile = self.profiles.get(Language.ENGLISH)
            if en_profile:
                templates = en_profile.templates.get(template_key, [])
        
        if not templates:
            return context.get("default", "")
        
        # Pick a random template for variety
        import random
        template = random.choice(templates)
        
        # Fill in context
        try:
            return template.format(**context)
        except KeyError:
            return template
    
    def get_exclamation(self, language: Optional[Language] = None) -> str:
        """Get a random exclamation in the active language"""
        lang = language or self.active_language
        profile = self.profiles.get(lang)
        
        if profile and profile.exclamations:
            import random
            return random.choice(profile.exclamations)
        
        return "WOW!"
    
    def get_filler(self, language: Optional[Language] = None) -> str:
        """Get filler phrase for silence"""
        lang = language or self.active_language
        profile = self.profiles.get(lang)
        
        if profile and profile.fillers:
            import random
            return random.choice(profile.fillers)
        
        return "Let's see what happens next..."
    
    def get_transition(self, language: Optional[Language] = None) -> str:
        """Get transition phrase"""
        lang = language or self.active_language
        profile = self.profiles.get(lang)
        
        if profile and profile.transitions:
            import random
            return random.choice(profile.transitions)
        
        return "Meanwhile..."
    
    def _get_llm_provider(self):
        """
        Lazily resolve the text-generation provider from backend/app/ai.
        Kept lazy so importing this module never triggers provider
        selection, network probes, or paid-SDK imports.
        """
        if self._llm_provider is None and not self._llm_provider_failed:
            try:
                try:
                    from backend.app.ai import get_llm_provider
                except ImportError:
                    # `app` package layout (backend/ on sys.path) — the
                    # layout the running server actually uses.
                    from app.ai import get_llm_provider
                self._llm_provider = get_llm_provider()
            except Exception as e:
                self._log.warning(
                    "AI provider layer unavailable; translation honestly "
                    "disabled (original text returned)",
                    error=str(e),
                )
                self._llm_provider_failed = True
        return self._llm_provider

    def translate(
        self,
        text: str,
        target_language: Language,
        source_language: Optional[Language] = None,
    ) -> TranslationResult:
        """
        Translate text to the target language.

        Honest behaviour:
          * If a capable LLM provider is active (default: local Ollama),
            the text is really translated and translated=True.
          * Otherwise the ORIGINAL text is returned unchanged with
            translated=False and provider="none". The old "[lang] prefix"
            hack is gone: untranslated text is never disguised.

        NOTE: return type changed from str to TranslationResult (no
        in-repo callers existed, verified 2026-10-17).
        """

        source = source_language or self.active_language

        def _passthrough(note: str) -> TranslationResult:
            return TranslationResult(
                text=text,
                translated=False,
                source_language=source.value,
                target_language=target_language.value,
                provider="none",
                note=note,
            )

        if not text or not text.strip():
            return _passthrough("empty input")

        if target_language == source:
            return _passthrough("source and target language are identical")

        cache_key = f"{text}_{target_language.value}"

        # Check cache (only real translations are ever cached)
        if cache_key in self.translation_cache:
            cached = self.translation_cache[cache_key]
            if time.time() - cached.cached_at < 3600:  # 1 hour cache
                return TranslationResult(
                    text=cached.translation,
                    translated=True,
                    source_language=source.value,
                    target_language=target_language.value,
                    provider="cache",
                    note="served from translation cache",
                )

        provider = self._get_llm_provider()
        if provider is None or not provider.supports_translation:
            # Heuristic templates must NEVER be presented as translations.
            return _passthrough(
                "no translation-capable provider available "
                f"(active: {getattr(provider, 'name', 'none')})"
            )

        try:
            if not provider.available():
                return _passthrough(
                    f"provider '{provider.name}' not reachable"
                )
            prompt = (
                f"Translate the following esports commentary from "
                f"{source.value} to {target_language.value}. "
                f"Return ONLY the translation, no explanations, no quotes.\n\n"
                f"{text}"
            )
            translated = provider.generate(prompt, max_tokens=200, temperature=0.3)
            translated = (translated or "").strip()
            if not translated:
                return _passthrough("provider returned empty text")
        except Exception as e:
            self._log.warning("Translation failed; returning original text",
                              error=str(e))
            return _passthrough(f"provider error: {e}")

        # Cache only genuine translations.
        self.translation_cache[cache_key] = TranslationCache(
            key=cache_key,
            translation=translated,
            language=target_language,
        )

        return TranslationResult(
            text=translated,
            translated=True,
            source_language=source.value,
            target_language=target_language.value,
            provider=provider.last_source(),
            note="translated by LLM provider",
        )
    
    def get_language_name(self, language: Language) -> str:
        """Get human-readable language name"""
        profile = self.profiles.get(language)
        return profile.name if profile else language.value
    
    def get_stats(self) -> Dict[str, Any]:
        """Get language manager statistics"""
        return {
            "active_language": self.active_language.value,
            "secondary_language": self.secondary_language.value if self.secondary_language else None,
            "available_languages": [
                {
                    "code": lang.value,
                    "name": profile.name,
                    "active": profile.is_active,
                    "templates": len(profile.templates),
                }
                for lang, profile in self.profiles.items()
            ],
            "translation_cache_size": len(self.translation_cache),
        }
    
    def reset(self) -> None:
        """Reset language manager"""
        self.translation_cache.clear()
        self.active_language = Language.HINGLISH
        self._log.info("Language manager reset")

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BaseAgent lifecycle
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

    async def setup(self) -> None:
        """Subscribe to match events relevant to this agent."""
        self.subscribe_to_event("match_state_change")

    async def tick(self) -> None:
        """Periodic maintenance tick."""
        # Heuristic engine is driven by direct method calls;
        # nothing periodic is required here.

    async def handle_event(self, record: EventRecord) -> None:
        """Handle an event from the event bus.

        The heuristic engine is driven synchronously via direct method
        calls; this hook keeps match context current for future
        event-driven use.
        """
        try:
            data = record.event.data if hasattr(record, "event") else {}
            if isinstance(data, dict) and data.get("match_id"):
                self.config.match_id = data["match_id"]
        except Exception:
            pass

    async def cleanup(self) -> None:
        """Release agent resources."""
