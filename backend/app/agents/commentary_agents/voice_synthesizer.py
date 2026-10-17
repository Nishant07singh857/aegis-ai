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
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
from ..base_agent import BaseAgent, AgentConfig
from ...core.event_bus import EventBus, EventRecord

logger = structlog.get_logger("aegis.agents.voice")

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
      * naturalness_score  -> 0.0 means "not measured"; it is only set to a
        real value when the engine actually reports one. Never hardcode it.
    """
    audio_id: str
    text: str
    audio_data: Optional[bytes] = None
    audio_url: Optional[str] = None
    duration_seconds: float = 0.0
    synthesized: bool = False

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
        
        # Primary Hindi caster (engine configured via AEGIS_TTS_ENGINE; NONE by default)
        self.profiles["hindi_caster_1"] = VoiceProfile(
            profile_id="hindi_caster_1",
            name="Aryan - Hindi Hype Caster",
            engine=TTSEngine.NONE,  # honest default: no engine configured
            voice_id="eleven_hindi_hype_01",
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
        
        # English analyst caster (engine configured via AEGIS_TTS_ENGINE; NONE by default)
        self.profiles["english_analyst"] = VoiceProfile(
            profile_id="english_analyst",
            name="James - English Analyst",
            engine=TTSEngine.NONE,  # honest default: no engine configured
            voice_id="eleven_english_analyst_01",
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
        
        # Hinglish caster (engine configured via AEGIS_TTS_ENGINE; NONE by default)
        self.profiles["hinglish_caster"] = VoiceProfile(
            profile_id="hinglish_caster",
            name="Rahul - Hinglish Caster",
            engine=TTSEngine.NONE,  # honest default: no engine configured
            voice_id="eleven_hinglish_01",
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
        
        # Get profile
        profile_id = profile_id or self.active_profile_id
        profile = self.profiles.get(profile_id)
        
        if not profile:
            self._log.error(f"Voice profile not found: {profile_id}")
            return None
        
        # Check cache
        cache_key = self._get_cache_key(text, profile_id, style)
        if cache and cache_key in self.audio_cache:
            self.total_cache_hits += 1
            return self.audio_cache[cache_key]
        
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
        
        return None
    
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

        engine = os.environ.get("AEGIS_TTS_ENGINE", "none").strip().lower()

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

        model_path = os.environ.get("AEGIS_TTS_MODEL_PATH", "").strip()
        if not model_path or not os.path.exists(model_path):
            self._log.error(
                "AEGIS_TTS_ENGINE=piper but AEGIS_TTS_MODEL_PATH is missing "
                "or not a file. Download a voice (python -m "
                "piper.download_voices <voice>) and point AEGIS_TTS_MODEL_PATH "
                "at the .onnx file."
            )
            return None

        try:
            start_time = time.time()
            voice = PiperVoice.load(model_path)

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
        engine = os.environ.get("AEGIS_TTS_ENGINE", "none").strip().lower() or "none"
        return {
            "total_synthesized": self.total_synthesized,
            "cache_hits": self.total_cache_hits,
            "cache_size": len(self.audio_cache),
            "avg_latency_ms": round(self.total_latency_ms, 1),
            "active_profile": self.active_profile_id,
            # Honest engine status: "none" means synthesis is disabled.
            "tts_engine": engine,
            "synthesis_enabled": engine not in ("", "none"),
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
