# backend/app/agents/commentary_agents/voice_synthesizer.py
"""
AEGIS AI - Voice Synthesizer
Converts commentary text to speech using TTS engines.
Supports multiple voices, languages, and emotional modulation.

HONEST STATUS: no TTS engine is wired by default. synthesize() returns
None (synthesized=False) and logs a clear warning until the operator
configures a LOCAL engine. Nothing here fakes audio.

Local (free) TTS options -- documented, NOT installed as dependencies:
  * Piper (recommended):  pip install piper-tts
                          download a voice, e.g.:
                            python -m piper.download_voices en_US-lessac-medium
                          then set:
                            AEGIS_TTS_ENGINE=piper
                            AEGIS_TTS_MODEL_PATH=/path/to/en_US-lessac-medium.onnx
                          (a sibling .onnx.json config file must sit next to
                          the .onnx file; piper-tts downloads both together)
  * Coqui TTS:            pip install TTS
                          then set AEGIS_TTS_ENGINE=coqui
                          (wiring not implemented yet -- see _call_tts_engine)

Paid cloud engines (ElevenLabs/Azure/Google) are intentionally NOT wired:
the project has a zero-paid-API-spend constraint. Their enum values remain
as documentation of what VoiceProfile.engine *could* hold one day behind
an explicit opt-in, the same way backend/app/ai/optional_paid.py gates
paid LLM providers.
"""

import asyncio
import os
import re
import time
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.voice")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PIPER VOICE ROUTING (Phase 21 / T5a)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Local Piper voices live in one directory (default: <repo>/.local-voices,
# override with AEGIS_VOICES_DIR). Routing is by language: English lines
# use the English voice, Hindi/Hinglish lines use the Hindi voice. A voice
# that is not on disk is simply unavailable — routing never fakes one.

#: language code -> Piper voice file in the voices directory.
PIPER_VOICE_FILES: Dict[str, str] = {
    "hi": "hi_IN-pratham-medium.onnx",  # Hindi (also reads Hinglish)
    "en": "en_US-amy-medium.onnx",  # English (US)
}

#: HuggingFace source of the free voices (rhasspy/piper-voices).
PIPER_VOICE_URLS: Dict[str, str] = {
    "hi": "https://huggingface.co/rhasspy/piper-voices/resolve/main/hi/hi_IN/pratham/medium/hi_IN-pratham-medium.onnx",
    "en": "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/medium/en_US-amy-medium.onnx",
}

# Roman-script Hindi (Hinglish) marker words. English commentary rarely
# contains two or more of these; Hinglish lines almost always do.
_HINGLISH_MARKERS = {
    "hai", "hain", "kya", "aur", "ne", "ka", "ki", "ke", "ko", "mein",
    "se", "bhi", "yeh", "woh", "ab", "toh", "nahi", "bahut", "khel",
    "raha", "rahe", "gaya", "liye", "wala", "wali", "diya", "hoga",
    "karega", "jeet", "haar", "mauka", "dhamaka",
}


def default_voices_dir() -> Path:
    """Directory holding the local Piper voices.

    ``$AEGIS_VOICES_DIR`` wins; otherwise ``<repo>/.local-voices`` (the
    directory the Hindi voice was installed into — see docs/AUDIO.md).
    """
    env = os.environ.get("AEGIS_VOICES_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(__file__).resolve().parents[4] / ".local-voices"


def piper_voice_path(language: str, voices_dir: Optional[Path] = None) -> Optional[Path]:
    """On-disk Piper model for a language, or None if not installed."""
    name = PIPER_VOICE_FILES.get((language or "").strip().lower())
    if not name:
        return None
    path = (voices_dir or default_voices_dir()) / name
    return path if path.exists() else None


def available_piper_voices(voices_dir: Optional[Path] = None) -> Dict[str, bool]:
    """Honest availability map: language -> voice file present on disk."""
    return {
        lang: piper_voice_path(lang, voices_dir) is not None
        for lang in PIPER_VOICE_FILES
    }


def resolve_piper_model_path(
    language: str, voices_dir: Optional[Path] = None
) -> Optional[str]:
    """Model path to synthesize ``language`` with, or None.

    Order: (1) the per-language voice in the voices directory;
    (2) the legacy single-model ``AEGIS_TTS_MODEL_PATH`` override;
    (3) None — synthesis stays honestly disabled for that language.
    """
    voice = piper_voice_path(language, voices_dir)
    if voice is not None:
        return str(voice)
    legacy = os.environ.get("AEGIS_TTS_MODEL_PATH", "").strip()
    if legacy and os.path.exists(legacy):
        return legacy
    return None


def detect_text_language(text: str) -> str:
    """Route a commentary line: ``"hi"`` for Hindi/Hinglish, else ``"en"``.

    Devanagari script is Hindi outright. Roman-script lines count as
    Hinglish when they carry >= 2 distinct Hinglish marker words —
    English lines essentially never do.
    """
    if any("\u0900" <= ch <= "\u097f" for ch in text):
        return "hi"
    tokens = re.findall(r"[a-z]+", text.lower())
    markers = {t for t in tokens if t in _HINGLISH_MARKERS}
    return "hi" if len(markers) >= 2 else "en"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class VoiceGender(str, Enum):
    """Voice gender options"""
    MALE = "male"
    FEMALE = "female"
    NEUTRAL = "neutral"

class VoiceStyle(str, Enum):
    """Voice speaking styles"""
    NORMAL = "normal"
    EXCITED = "excited"
    CALM = "calm"
    DRAMATIC = "dramatic"
    WHISPER = "whisper"
    SHOUT = "shout"

class TTSEngine(str, Enum):
    """
    Supported TTS engines.

    NONE is the default: no engine configured, synthesis honestly disabled.
    PIPER is the supported local/free engine (see module docstring).
    The cloud values are documentation-only under the zero-paid-spend rule.
    """
    NONE = "none"
    PIPER = "piper"
    ELEVENLABS = "elevenlabs"
    AZURE = "azure"
    GOOGLE = "google"
    BARK = "bark"
    COQUI = "coqui"

@dataclass
class VoiceProfile:
    """Voice profile for a caster"""
    profile_id: str
    name: str
    # Default NONE: no engine is configured until the operator sets
    # AEGIS_TTS_ENGINE. Profiles must not claim a paid cloud engine.
    engine: TTSEngine = TTSEngine.NONE
    voice_id: str = ""
    gender: VoiceGender = VoiceGender.MALE
    language: str = "hi"  # Hindi by default for BGMI
    
    # Voice characteristics
    pitch: float = 1.0        # Pitch modifier (0.5-2.0)
    speed: float = 1.0        # Speaking speed (0.5-2.0)
    volume: float = 1.0       # Volume (0.0-1.0)
    stability: float = 0.5    # Voice stability (0.0-1.0)
    clarity: float = 0.75     # Voice clarity (0.0-1.0)
    
    # Style
    default_style: VoiceStyle = VoiceStyle.NORMAL
    supported_styles: List[VoiceStyle] = field(default_factory=lambda: [
        VoiceStyle.NORMAL, VoiceStyle.EXCITED, VoiceStyle.DRAMATIC,
    ])
    
    # Emotion modulation
    emotion_boost: float = 0.5  # How much emotion affects voice
    
    # Metadata
    is_active: bool = True
    usage_count: int = 0

@dataclass
class SynthesizedAudio:
    """
    Synthesized audio output.

    Honest-status contract:
      * synthesized=False  -> no real audio was produced (no engine
        configured, or synthesis failed). audio_data will be None.
      * synthesized=True   -> audio_data holds real bytes from a real engine.
      * source             -> provenance: "live" (synthesized for this
        exact text just now), "cache" (LRU hit for this exact text), or
        "cached_fallback" (live synthesis FAILED and a pre-rendered
        generic line was substituted — `text` is then the generic line
        actually spoken and `requested_text` the line that failed).
      * naturalness_score  -> 0.0 means "not measured"; it is only set to a
        real value when the engine actually reports one. Never hardcode it.
    """
    audio_id: str
    text: str
    audio_data: Optional[bytes] = None
    audio_url: Optional[str] = None
    duration_seconds: float = 0.0
    synthesized: bool = False
    source: str = "live"
    requested_text: Optional[str] = None

    # Voice used
    profile: Optional[VoiceProfile] = None

    # Metadata
    generated_at: float = field(default_factory=time.time)
    engine_used: TTSEngine = TTSEngine.NONE
    latency_ms: float = 0.0

    # Quality (0.0 = not measured / no real engine)
    naturalness_score: float = 0.0


class VoiceSynthesizer(BaseAgent):
    """
    Converts commentary text to speech.

    Honest-status design: synthesis is DISABLED until the operator sets
    AEGIS_TTS_ENGINE to a real local engine (currently: piper). Until then
    synthesize() returns None -- never fake audio, never a fake quality
    score. See the module docstring for local setup instructions.

    Features:
    - Pluggable TTS engines (local Piper supported; cloud engines
      intentionally not wired under the zero-paid-spend constraint)
    - Multiple voice profiles (play-by-play, analyst, Hindi)
    - Emotion-based voice modulation
    - Voice caching for common phrases
    - Batch synthesis for efficiency
    - Audio post-processing (pitch, speed)
    """
    
    def __init__(self, config: AgentConfig, event_bus: EventBus):
        super().__init__(config, event_bus)
        # Voice profiles
        self.profiles: Dict[str, VoiceProfile] = {}
        
        # Audio cache
        self.audio_cache: Dict[str, SynthesizedAudio] = {}
        self.cache_max_size = 100
        
        # Synthesis queue
        self.synthesis_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        
        # Active profile
        self.active_profile_id: str = ""
        
        # Warn only once when synthesis is attempted with no engine
        self._no_engine_warned = False

        # Statistics
        self.total_synthesized: int = 0
        self.total_cache_hits: int = 0
        self.total_latency_ms: float = 0.0
        
        # Initialize default profiles
        self._initialize_profiles()
        
        self._log = logger.bind(component="voice_synthesizer")
    
    def _initialize_profiles(self) -> None:
        """Initialize default voice profiles"""
        
        # Primary Hindi caster — wired to the real installed Piper voice
        # (Phase 21/22: hi_IN-pratham-medium.onnx in the voices dir).
        self.profiles["hindi_caster_1"] = VoiceProfile(
            profile_id="hindi_caster_1",
            name="Aryan - Hindi Hype Caster",
            engine=TTSEngine.PIPER,
            voice_id="hi_IN-pratham-medium",
            gender=VoiceGender.MALE,
            language="hi",
            pitch=1.1,
            speed=1.1,
            stability=0.4,
            clarity=0.8,
            default_style=VoiceStyle.EXCITED,
            supported_styles=[
                VoiceStyle.NORMAL, VoiceStyle.EXCITED,
                VoiceStyle.DRAMATIC, VoiceStyle.SHOUT,
            ],
            emotion_boost=0.8,
        )
        
        # English analyst caster — wired to the real installed Piper voice
        # (Phase 21/22: en_US-amy-medium.onnx in the voices dir).
        self.profiles["english_analyst"] = VoiceProfile(
            profile_id="english_analyst",
            name="James - English Analyst",
            engine=TTSEngine.PIPER,
            voice_id="en_US-amy-medium",
            gender=VoiceGender.MALE,
            language="en",
            pitch=0.95,
            speed=0.9,
            stability=0.6,
            clarity=0.9,
            default_style=VoiceStyle.CALM,
            supported_styles=[
                VoiceStyle.NORMAL, VoiceStyle.CALM,
                VoiceStyle.EXCITED, VoiceStyle.DRAMATIC,
            ],
            emotion_boost=0.4,
        )
        
        # Hinglish caster — Hindi Piper voice reads Roman-script Hinglish
        # (Phase 21 routing decision; same installed voice file).
        self.profiles["hinglish_caster"] = VoiceProfile(
            profile_id="hinglish_caster",
            name="Rahul - Hinglish Caster",
            engine=TTSEngine.PIPER,
            voice_id="hi_IN-pratham-medium",
            gender=VoiceGender.MALE,
            language="hi",
            pitch=1.05,
            speed=1.0,
            stability=0.5,
            clarity=0.8,
            default_style=VoiceStyle.EXCITED,
            supported_styles=[
                VoiceStyle.NORMAL, VoiceStyle.EXCITED,
                VoiceStyle.DRAMATIC, VoiceStyle.SHOUT,
            ],
            emotion_boost=0.7,
        )
        
        # Set default active profile
        self.active_profile_id = "hinglish_caster"
        
        self._log.info(f"Initialized {len(self.profiles)} voice profiles")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # VOICE SYNTHESIS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def synthesize(
        self,
        text: str,
        profile_id: Optional[str] = None,
        style: Optional[VoiceStyle] = None,
        emotion_params: Optional[Dict[str, float]] = None,
        cache: bool = True,
    ) -> Optional[SynthesizedAudio]:
        """
        Synthesize text to speech.
        
        Args:
            text: Text to synthesize
            profile_id: Voice profile to use
            style: Speaking style
            emotion_params: Emotion modulation (energy, tension, etc.)
            cache: Whether to cache the result
            
        Returns:
            SynthesizedAudio or None if failed
        """
        
        # Get profile — explicit profile_id wins; otherwise route the
        # line to the right language profile (Phase 21/T5a).
        if profile_id is None:
            language = detect_text_language(text)
            routed = self.get_profile_for_language(language)
            profile_id = routed.profile_id if routed is not None else self.active_profile_id
        profile = self.profiles.get(profile_id)

        if not profile:
            self._log.error(f"Voice profile not found: {profile_id}")
            return None
        
        # Check cache
        cache_key = self._get_cache_key(text, profile_id, style)
        if cache and cache_key in self.audio_cache:
            self.total_cache_hits += 1
            hit = self.audio_cache[cache_key]
            if hit.source == "live":
                hit.source = "cache"
            return hit
        
        # Apply emotion modulation
        voice_params = self._apply_emotion_modulation(
            profile,
            style,
            emotion_params,
        )
        
        # Synthesize
        start_time = time.time()
        
        try:
            audio = await self._call_tts_engine(
                text=text,
                profile=profile,
                voice_params=voice_params,
            )
            
            latency = (time.time() - start_time) * 1000
            
            if audio:
                # Update stats
                self.total_synthesized += 1
                self.total_latency_ms = (
                    self.total_latency_ms * (self.total_synthesized - 1) + latency
                ) / self.total_synthesized
                
                audio.latency_ms = latency
                
                # Cache the result
                if cache:
                    self._add_to_cache(cache_key, audio)
                
                self._log.debug(
                    f"Synthesized: {len(text)} chars",
                    profile=profile.name,
                    latency_ms=round(latency, 1),
                )
                
                return audio
            
        except Exception as e:
            self._log.error(f"Synthesis failed: {e}")

        # Phase 22 fallback: live synthesis was configured (Piper) but
        # failed — substitute a pre-rendered generic line from the
        # fallback pack, honestly labelled source="cached_fallback".
        # Only when a Piper attempt was intended; an explicitly disabled
        # engine (env "none") stays silent.
        env_engine = os.environ.get("AEGIS_TTS_ENGINE")
        intended_piper = (
            env_engine.strip().lower() == "piper"
            if env_engine is not None
            else profile.engine == TTSEngine.PIPER
        )
        if intended_piper:
            fallback = self._fallback_from_pack(text, profile)
            if fallback is not None:
                return fallback

        return None

    def _fallback_from_pack(
        self, requested_text: str, profile: VoiceProfile
    ) -> Optional[SynthesizedAudio]:
        """Load a pre-rendered generic line for the profile's language."""
        try:
            from app.audio.fallback_pack import (
                fallback_text_for,
                find_fallback_wav,
            )

            wav_path = find_fallback_wav(profile.language)
            if wav_path is None:
                return None
            audio_bytes = wav_path.read_bytes()
            duration = max(1.5, len(requested_text.split()) / 3.0)
            try:
                import io
                import wave

                with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                    frames = wav.getnframes()
                    rate = wav.getframerate() or 22050
                    duration = frames / rate
            except Exception:
                pass
            generic_text = fallback_text_for(wav_path) or requested_text
            self._log.warning(
                "live synthesis failed; serving cached fallback line",
                language=profile.language,
                fallback_file=wav_path.name,
            )
            return SynthesizedAudio(
                audio_id=hashlib.md5(
                    f"fallback_{wav_path.name}_{time.time()}".encode()
                ).hexdigest()[:16],
                text=generic_text,
                audio_data=audio_bytes,
                duration_seconds=duration,
                synthesized=True,  # real pre-rendered Piper audio
                source="cached_fallback",
                requested_text=requested_text,
                profile=profile,
                engine_used=TTSEngine.PIPER,
            )
        except Exception as e:
            self._log.error(f"Fallback pack lookup failed: {e}")
            return None

    async def warm_models(self) -> Dict[str, bool]:
        """Load each installed Piper voice once (pays the cold model-load
        cost at boot / match start instead of on the first live line)."""
        warmed: Dict[str, bool] = {}
        for lang in PIPER_VOICE_FILES:
            path = resolve_piper_model_path(lang)
            if not path:
                warmed[lang] = False
                continue
            try:
                from piper import PiperVoice

                voice = await asyncio.to_thread(PiperVoice.load, path)
                cache = getattr(self, "_voice_cache", None)
                if cache is None:
                    cache = self._voice_cache = {}
                cache[path] = voice
                warmed[lang] = True
            except Exception as e:
                self._log.warning(f"voice warm-up failed for {lang}: {e}")
                warmed[lang] = False
        return warmed

    async def synthesize_batch(
        self,
        lines: List[Tuple[str, Optional[str], Optional[VoiceStyle]]],
    ) -> List[Optional[SynthesizedAudio]]:
        """Synthesize multiple lines in batch"""
        
        tasks = []
        for text, profile_id, style in lines:
            tasks.append(self.synthesize(text, profile_id, style))
        
        return await asyncio.gather(*tasks)
    
    async def _call_tts_engine(
        self,
        text: str,
        profile: VoiceProfile,
        voice_params: Dict[str, Any],
    ) -> Optional[SynthesizedAudio]:
        """
        Call the configured TTS engine for synthesis.

        Returns None when no engine is configured -- this is the HONEST
        disabled state (callers see synthesized=False / None, never fake
        audio). A real SynthesizedAudio with audio bytes is returned only
        when a real engine actually produced audio.
        """

        # Engine resolution (Phase 22): an explicit AEGIS_TTS_ENGINE env
        # wins (including "none" = deliberately disabled). When the env is
        # UNSET, the profile's own engine applies — profiles are wired to
        # the local Piper voices, so routing works out of the box when the
        # voice files are installed, and stays honestly silent when they
        # are not (resolve_piper_model_path returns None).
        if "AEGIS_TTS_ENGINE" in os.environ:
            engine = os.environ.get("AEGIS_TTS_ENGINE", "none").strip().lower()
        else:
            engine = profile.engine.value

        if engine in ("", "none"):
            if not self._no_engine_warned:
                self._log.warning(
                    "No TTS engine configured: voice synthesis is honestly "
                    "DISABLED (synthesize() returns None). To enable local "
                    "free TTS: pip install piper-tts, download a voice, then "
                    "set AEGIS_TTS_ENGINE=piper and "
                    "AEGIS_TTS_MODEL_PATH=/path/to/voice.onnx"
                )
                self._no_engine_warned = True
            return None

        if engine == "piper":
            return await self._synthesize_with_piper(text, profile, voice_params)

        # Anything else (elevenlabs/azure/google/coqui/...) is not wired:
        # say so plainly instead of faking audio.
        self._log.warning(
            f"TTS engine '{engine}' is not implemented; synthesis disabled. "
            "Supported local value: AEGIS_TTS_ENGINE=piper "
            "(see module docstring). Paid cloud engines are intentionally "
            "not wired (zero-paid-spend constraint)."
        )
        return None

    async def _synthesize_with_piper(
        self,
        text: str,
        profile: VoiceProfile,
        voice_params: Dict[str, Any],
    ) -> Optional[SynthesizedAudio]:
        """
        Real synthesis via local Piper TTS (free, offline).

        Requires: pip install piper-tts, plus
            AEGIS_TTS_MODEL_PATH=/path/to/<voice>.onnx
        (with the matching <voice>.onnx.json beside it).
        The piper_sdk import is lazy so the dependency stays optional.
        """
        try:
            from piper import PiperVoice  # lazy: optional dependency
        except ImportError:
            self._log.error(
                "AEGIS_TTS_ENGINE=piper but piper-tts is not installed. "
                "Run: pip install piper-tts (synthesis disabled until then)."
            )
            return None

        model_path = resolve_piper_model_path(profile.language)
        if not model_path:
            self._log.error(
                "AEGIS_TTS_ENGINE=piper but no Piper voice is available for "
                f"language '{profile.language}'. Expected "
                f"{PIPER_VOICE_FILES.get(profile.language, '<unknown voice>')} "
                f"in {default_voices_dir()} (download: "
                f"{PIPER_VOICE_URLS.get(profile.language, 'see docs/AUDIO.md')}), "
                "or set AEGIS_TTS_MODEL_PATH to a .onnx voice file."
            )
            return None

        try:
            start_time = time.time()
            # Phase 22 latency fix: cache loaded voices per model path.
            # Reloading the ONNX voice on EVERY line was most of the warm
            # synthesis cost; a loaded PiperVoice is reusable across lines.
            cache = getattr(self, "_voice_cache", None)
            if cache is None:
                cache = self._voice_cache = {}
            voice = cache.get(model_path)
            if voice is None:
                voice = PiperVoice.load(model_path)
                cache[model_path] = voice

            # Piper synthesis is blocking; keep it off the event loop.
            def _render() -> bytes:
                import io
                import wave

                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav:
                    voice.synthesize_wav(text, wav)
                return buf.getvalue()

            audio_bytes = await asyncio.to_thread(_render)
            latency_ms = (time.time() - start_time) * 1000.0

            # Duration from the WAV header when possible; estimate otherwise.
            duration = max(1.5, len(text.split()) / 3.0)
            try:
                import io
                import wave

                with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
                    frames = wav.getnframes()
                    rate = wav.getframerate() or 22050
                    duration = frames / rate
            except Exception:
                pass

            return SynthesizedAudio(
                audio_id=hashlib.md5(
                    f"{text}_{profile.profile_id}_{time.time()}".encode()
                ).hexdigest()[:16],
                text=text,
                audio_data=audio_bytes,
                duration_seconds=duration,
                synthesized=True,  # real bytes from a real engine
                profile=profile,
                engine_used=TTSEngine.PIPER,
                latency_ms=latency_ms,
                naturalness_score=0.0,  # not measured; never hardcode
            )
        except Exception as e:
            self._log.error(f"Piper TTS synthesis failed: {e}")
            return None
    
    def _apply_emotion_modulation(
        self,
        profile: VoiceProfile,
        style: Optional[VoiceStyle],
        emotion_params: Optional[Dict[str, float]],
    ) -> Dict[str, Any]:
        """Apply emotion modulation to voice parameters"""
        
        params = {
            "pitch": profile.pitch,
            "speed": profile.speed,
            "volume": profile.volume,
            "stability": profile.stability,
            "clarity": profile.clarity,
            "style": 0.5,  # Neutral style
        }
        
        # Apply speaking style
        if style:
            style_modifiers = {
                VoiceStyle.EXCITED: {"pitch": 0.15, "speed": 0.2, "volume": 0.15, "style": 0.8},
                VoiceStyle.DRAMATIC: {"pitch": 0.1, "speed": -0.1, "volume": 0.1, "style": 0.7},
                VoiceStyle.CALM: {"pitch": -0.05, "speed": -0.15, "volume": -0.1, "style": 0.3},
                VoiceStyle.SHOUT: {"pitch": 0.2, "speed": 0.1, "volume": 0.3, "style": 0.9},
                VoiceStyle.WHISPER: {"pitch": -0.1, "speed": -0.2, "volume": -0.5, "style": 0.2},
            }
            
            modifiers = style_modifiers.get(style, {})
            for key, value in modifiers.items():
                if key in params:
                    params[key] += value * profile.emotion_boost
        
        # Apply emotion parameters
        if emotion_params:
            energy = emotion_params.get("energy", 50) / 100
            tension = emotion_params.get("tension", 20) / 100
            
            params["speed"] += energy * 0.15
            params["pitch"] += energy * 0.1
            params["volume"] += energy * 0.1
            params["stability"] -= tension * 0.1
        
        # Clamp values
        params["pitch"] = max(0.5, min(2.0, params["pitch"]))
        params["speed"] = max(0.5, min(2.0, params["speed"]))
        params["volume"] = max(0.0, min(1.0, params["volume"]))
        params["stability"] = max(0.0, min(1.0, params["stability"]))
        params["clarity"] = max(0.0, min(1.0, params["clarity"]))
        params["style"] = max(0.0, min(1.0, params["style"]))
        
        return params
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CACHE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def _get_cache_key(self, text: str, profile_id: str, style: Optional[VoiceStyle]) -> str:
        """Generate cache key"""
        key_parts = [text, profile_id, style.value if style else "default"]
        return hashlib.md5("_".join(key_parts).encode()).hexdigest()
    
    def _add_to_cache(self, key: str, audio: SynthesizedAudio) -> None:
        """Add to audio cache"""
        if len(self.audio_cache) >= self.cache_max_size:
            # Remove oldest entry
            oldest_key = next(iter(self.audio_cache))
            del self.audio_cache[oldest_key]
        
        self.audio_cache[key] = audio
    
    def clear_cache(self) -> None:
        """Clear audio cache"""
        self.audio_cache.clear()
        self._log.info("Audio cache cleared")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PROFILE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_profile(self, profile: VoiceProfile) -> None:
        """Add a voice profile"""
        self.profiles[profile.profile_id] = profile
        self._log.info(f"Voice profile added: {profile.name}")
    
    def set_active_profile(self, profile_id: str) -> bool:
        """Set active voice profile"""
        if profile_id in self.profiles:
            self.active_profile_id = profile_id
            self._log.info(f"Active profile: {self.profiles[profile_id].name}")
            return True
        return False
    
    def get_active_profile(self) -> Optional[VoiceProfile]:
        """Get active voice profile"""
        return self.profiles.get(self.active_profile_id)
    
    def get_profile_for_language(self, language: str) -> Optional[VoiceProfile]:
        """Get best voice profile for a language"""
        for profile in self.profiles.values():
            if profile.language == language and profile.is_active:
                return profile
        return self.get_active_profile()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get synthesizer statistics"""
        if "AEGIS_TTS_ENGINE" in os.environ:
            engine = os.environ.get("AEGIS_TTS_ENGINE", "none").strip().lower() or "none"
        else:
            # Profiles default to Piper; enabled iff a voice is installed.
            engine = "piper" if any(available_piper_voices().values()) else "none"
        return {
            "total_synthesized": self.total_synthesized,
            "cache_hits": self.total_cache_hits,
            "cache_size": len(self.audio_cache),
            "avg_latency_ms": round(self.total_latency_ms, 1),
            "active_profile": self.active_profile_id,
            # Honest engine status: "none" means synthesis is disabled.
            "tts_engine": engine,
            "synthesis_enabled": engine not in ("", "none"),
            # Phase 21/T5a: per-language Piper voice availability on disk.
            "voices_dir": str(default_voices_dir()),
            "voices": available_piper_voices(),
            "profiles": {
                pid: {
                    "name": p.name,
                    "language": p.language,
                    "gender": p.gender.value,
                    "active": p.is_active,
                    "usage_count": p.usage_count,
                }
                for pid, p in self.profiles.items()
            },
        }
    
    def reset(self) -> None:
        """Reset synthesizer"""
        self.clear_cache()
        self.total_synthesized = 0
        self.total_cache_hits = 0
        self.total_latency_ms = 0.0
        self._log.info("Voice synthesizer reset")

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
