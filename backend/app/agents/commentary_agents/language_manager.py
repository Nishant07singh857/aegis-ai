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


class LanguageManager:
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
    
    def __init__(self):
        # Language profiles
        self.profiles: Dict[Language, LanguageProfile] = {}
        
        # Active language
        self.active_language: Language = Language.HINGLISH
        self.secondary_language: Optional[Language] = Language.ENGLISH
        
        # Translation cache
        self.translation_cache: Dict[str, TranslationCache] = {}
        
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
    
    def translate(
        self,
        text: str,
        target_language: Language,
        source_language: Optional[Language] = None,
    ) -> str:
        """Translate text to target language"""
        
        cache_key = f"{text}_{target_language.value}"
        
        # Check cache
        if cache_key in self.translation_cache:
            cached = self.translation_cache[cache_key]
            if time.time() - cached.cached_at < 3600:  # 1 hour cache
                return cached.translation
        
        # In production, call translation API
        # For now, return original with language marker
        translated = f"[{target_language.value}] {text}"
        
        # Cache it
        self.translation_cache[cache_key] = TranslationCache(
            key=cache_key,
            translation=translated,
            language=target_language,
        )
        
        return translated
    
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