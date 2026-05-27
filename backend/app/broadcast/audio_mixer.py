# backend/app/broadcast/audio_mixer.py
"""
AEGIS AI - Audio Mixer
Manages game audio, commentary, and effects mixing.
Handles volume control, ducking, and audio routing.
"""

from typing import Dict, Any
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.audio")


class AudioChannel(str, Enum):
    """Audio channel types"""
    MASTER = "master"
    GAME = "game"
    COMMENTARY = "commentary"
    MUSIC = "music"
    EFFECTS = "effects"
    MIC = "mic"
    REPLAY = "replay"


@dataclass
class AudioSource:
    """An audio source in the mixer"""
    name: str
    channel: AudioChannel
    volume: float = 1.0
    muted: bool = False
    ducking_enabled: bool = False
    ducking_reduction: float = 0.5  # Reduce to 50% when ducked
    solo: bool = False


class AudioMixer:
    """
    Professional audio mixer for broadcast.
    
    Features:
    - Per-channel volume control
    - Audio ducking (auto-lower game during commentary)
    - Commentary priority mixing
    - Transition sounds
    - Hype stinger effects
    - Audio monitoring
    """
    
    def __init__(self, obs_client: Any):
        self.obs = obs_client
        self.sources: Dict[str, AudioSource] = {}
        self.channel_volumes: Dict[AudioChannel, float] = {
            ch: 1.0 for ch in AudioChannel
        }
        
        # Ducking state
        self.is_ducking: bool = False
        self.ducking_target: str = ""
        
        # Audio presets
        self.presets: Dict[str, Dict[str, float]] = {}
        
        self._log = logger.bind(component="audio_mixer")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SOURCE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def add_source(self, name: str, channel: AudioChannel, volume: float = 1.0) -> bool:
        """Add an audio source"""
        
        self.sources[name] = AudioSource(
            name=name,
            channel=channel,
            volume=volume,
        )
        
        # Set initial volume in OBS
        await self.obs.set_volume(name, volume * self.channel_volumes[channel])
        
        self._log.info(f"Audio source added: {name} ({channel.value})")
        return True
    
    async def remove_source(self, name: str) -> bool:
        """Remove an audio source"""
        self.sources.pop(name, None)
        return True
    
    async def set_source_volume(self, name: str, volume: float) -> bool:
        """Set individual source volume"""
        if name in self.sources:
            self.sources[name].volume = volume
            
            channel_vol = self.channel_volumes.get(
                self.sources[name].channel, 1.0
            )
            
            await self.obs.set_volume(name, volume * channel_vol)
            return True
        return False
    
    async def set_source_mute(self, name: str, muted: bool) -> bool:
        """Mute/unmute a source"""
        if name in self.sources:
            self.sources[name].muted = muted
            await self.obs.set_mute(name, muted)
            return True
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CHANNEL MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def set_channel_volume(self, channel: AudioChannel, volume: float) -> None:
        """Set volume for an entire channel"""
        
        self.channel_volumes[channel] = volume
        
        # Update all sources in this channel
        for source in self.sources.values():
            if source.channel == channel and not source.muted:
                await self.obs.set_volume(
                    source.name,
                    source.volume * volume,
                )
        
        self._log.info(f"Channel volume: {channel.value} = {volume:.2f}")
    
    async def mute_channel(self, channel: AudioChannel) -> None:
        """Mute an entire channel"""
        for source in self.sources.values():
            if source.channel == channel:
                await self.obs.set_mute(source.name, True)
    
    async def unmute_channel(self, channel: AudioChannel) -> None:
        """Unmute an entire channel"""
        for source in self.sources.values():
            if source.channel == channel:
                await self.obs.set_mute(source.name, False)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DUCKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def duck_for_commentary(self, enable: bool = True) -> None:
        """Duck game audio when commentary is active"""
        
        self.is_ducking = enable
        
        for source in self.sources.values():
            if source.channel in [AudioChannel.GAME, AudioChannel.MUSIC]:
                if enable:
                    # Reduce volume
                    new_vol = source.volume * source.ducking_reduction
                    await self.obs.set_volume(source.name, new_vol)
                else:
                    # Restore volume
                    new_vol = source.volume * self.channel_volumes[source.channel]
                    await self.obs.set_volume(source.name, new_vol)
        
        self._log.info(f"Audio ducking: {'ON' if enable else 'OFF'}")
    
    async def duck_for_replay(self) -> None:
        """Duck other audio during replay"""
        
        for source in self.sources.values():
            if source.channel not in [AudioChannel.REPLAY, AudioChannel.MASTER]:
                new_vol = source.volume * 0.3  # Reduce to 30%
                await self.obs.set_volume(source.name, new_vol)
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PRESETS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def save_preset(self, name: str) -> None:
        """Save current mixer state as preset"""
        self.presets[name] = {
            name: source.volume
            for name, source in self.sources.items()
        }
    
    async def load_preset(self, name: str) -> bool:
        """Load a mixer preset"""
        if name not in self.presets:
            return False
        
        for source_name, volume in self.presets[name].items():
            await self.set_source_volume(source_name, volume)
        
        return True
    
    def get_stats(self) -> Dict[str, Any]:
        """Get audio mixer statistics"""
        return {
            "sources": len(self.sources),
            "channels": {
                ch.value: {
                    "volume": round(self.channel_volumes.get(ch, 1.0), 2),
                    "sources": len([s for s in self.sources.values() if s.channel == ch]),
                }
                for ch in AudioChannel
            },
            "ducking": self.is_ducking,
            "presets": list(self.presets.keys()),
        }