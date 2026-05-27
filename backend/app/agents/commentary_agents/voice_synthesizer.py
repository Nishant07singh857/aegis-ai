# backend/app/agents/commentary_agents/voice_synthesizer.py
"""
AEGIS AI - Voice Synthesizer
Converts commentary text to speech using TTS engines.
Supports multiple voices, languages, and emotional modulation.
"""

import asyncio
import time
import hashlib
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog

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
    """Supported TTS engines"""
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
    engine: TTSEngine = TTSEngine.ELEVENLABS
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
    """Synthesized audio output"""
    audio_id: str
    text: str
    audio_data: Optional[bytes] = None
    audio_url: Optional[str] = None
    duration_seconds: float = 0.0
    
    # Voice used
    profile: Optional[VoiceProfile] = None
    
    # Metadata
    generated_at: float = field(default_factory=time.time)
    engine_used: TTSEngine = TTSEngine.ELEVENLABS
    latency_ms: float = 0.0
    
    # Quality
    naturalness_score: float = 0.8


class VoiceSynthesizer:
    """
    Converts commentary text to speech.
    
    Features:
    - Multiple TTS engine support (ElevenLabs primary)
    - Multiple voice profiles (play-by-play, analyst, Hindi)
    - Emotion-based voice modulation
    - Voice caching for common phrases
    - Batch synthesis for efficiency
    - Audio post-processing (pitch, speed)
    - Real-time streaming support
    """
    
    def __init__(self):
        # Voice profiles
        self.profiles: Dict[str, VoiceProfile] = {}
        
        # Audio cache
        self.audio_cache: Dict[str, SynthesizedAudio] = {}
        self.cache_max_size = 100
        
        # Synthesis queue
        self.synthesis_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
        
        # Active profile
        self.active_profile_id: str = ""
        
        # Statistics
        self.total_synthesized: int = 0
        self.total_cache_hits: int = 0
        self.total_latency_ms: float = 0.0
        
        # Initialize default profiles
        self._initialize_profiles()
        
        self._log = logger.bind(component="voice_synthesizer")
    
    def _initialize_profiles(self) -> None:
        """Initialize default voice profiles"""
        
        # Primary Hindi caster (ElevenLabs)
        self.profiles["hindi_caster_1"] = VoiceProfile(
            profile_id="hindi_caster_1",
            name="Aryan - Hindi Hype Caster",
            engine=TTSEngine.ELEVENLABS,
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
        
        # English analyst caster
        self.profiles["english_analyst"] = VoiceProfile(
            profile_id="english_analyst",
            name="James - English Analyst",
            engine=TTSEngine.ELEVENLABS,
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
        
        # Hinglish caster
        self.profiles["hinglish_caster"] = VoiceProfile(
            profile_id="hinglish_caster",
            name="Rahul - Hinglish Caster",
            engine=TTSEngine.ELEVENLABS,
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
        """Call the TTS engine for synthesis"""
        
        # In production, this would call:
        # - ElevenLabs API
        # - Azure Speech Services
        # - Google Cloud TTS
        # - Local Bark/Coqui model
        
        try:
            # Simulated TTS call (replace with actual API)
            
            # ElevenLabs example:
            # response = await elevenlabs.generate(
            #     text=text,
            #     voice=profile.voice_id,
            #     model="eleven_multilingual_v2",
            #     voice_settings={
            #         "stability": voice_params.get("stability", 0.5),
            #         "similarity_boost": voice_params.get("clarity", 0.75),
            #         "style": voice_params.get("style", 0.5),
            #         "use_speaker_boost": True,
            #     },
            # )
            
            # Estimate duration based on text length
            words = len(text.split())
            duration = max(1.5, words / 3.0 * voice_params.get("speed", 1.0))
            
            audio = SynthesizedAudio(
                audio_id=hashlib.md5(
                    f"{text}_{profile.profile_id}_{time.time()}".encode()
                ).hexdigest()[:16],
                text=text,
                duration_seconds=duration,
                profile=profile,
                engine_used=profile.engine,
                naturalness_score=0.85,
            )
            
            return audio
            
        except Exception as e:
            self._log.error(f"TTS engine error: {e}")
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
        return {
            "total_synthesized": self.total_synthesized,
            "cache_hits": self.total_cache_hits,
            "cache_size": len(self.audio_cache),
            "avg_latency_ms": round(self.total_latency_ms, 1),
            "active_profile": self.active_profile_id,
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