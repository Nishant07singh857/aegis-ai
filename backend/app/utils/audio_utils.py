# backend/app/utils/audio_utils.py
"""
AEGIS AI - Audio Processing Utilities
Audio capture, mixing, effects, and stream handling.
"""

from typing import List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import structlog
import numpy as np

logger = structlog.get_logger("aegis.utils.audio")


class AudioFormat(str, Enum):
    """Audio formats"""
    WAV = "wav"
    MP3 = "mp3"
    AAC = "aac"
    OGG = "ogg"
    FLAC = "flac"


@dataclass
class AudioConfig:
    """Audio processing configuration"""
    sample_rate: int = 44100
    channels: int = 2
    bit_depth: int = 16
    format: AudioFormat = AudioFormat.WAV


class AudioUtils:
    """Audio processing utility functions"""
    
    @staticmethod
    def normalize_audio(data: np.ndarray, target_db: float = -3.0) -> np.ndarray:
        """Normalize audio to target dB level"""
        rms = np.sqrt(np.mean(data ** 2))
        if rms > 0:
            target_rms = 10 ** (target_db / 20)
            return data * (target_rms / rms)
        return data
    
    @staticmethod
    def mix_audio(
        tracks: List[np.ndarray],
        volumes: Optional[List[float]] = None,
    ) -> np.ndarray:
        """Mix multiple audio tracks"""
        if not tracks:
            return np.array([])
        
        max_len = max(len(t) for t in tracks)
        mixed = np.zeros(max_len)
        
        for i, track in enumerate(tracks):
            vol = volumes[i] if volumes and i < len(volumes) else 1.0
            
            if len(track) < max_len:
                track = np.pad(track, (0, max_len - len(track)))
            
            mixed += track * vol
        
        # Prevent clipping
        max_val = np.abs(mixed).max()
        if max_val > 0.95:
            mixed = mixed / max_val * 0.95
        
        return mixed
    
    @staticmethod
    def apply_fade(audio: np.ndarray, fade_in_ms: float = 10, fade_out_ms: float = 100, sample_rate: int = 44100) -> np.ndarray:
        """Apply fade in/out effect"""
        fade_in_samples = int(sample_rate * fade_in_ms / 1000)
        fade_out_samples = int(sample_rate * fade_out_ms / 1000)
        
        result = audio.copy()
        
        # Fade in
        if fade_in_samples > 0 and len(result) > fade_in_samples:
            fade_in = np.linspace(0, 1, min(fade_in_samples, len(result)))
            result[:len(fade_in)] *= fade_in
        
        # Fade out
        if fade_out_samples > 0 and len(result) > fade_out_samples:
            fade_out = np.linspace(1, 0, min(fade_out_samples, len(result)))
            result[-len(fade_out):] *= fade_out
        
        return result
    
    @staticmethod
    def detect_silence(audio: np.ndarray, threshold: float = 0.01, min_duration_ms: float = 500, sample_rate: int = 44100) -> List[Tuple[float, float]]:
        """Detect silent regions"""
        min_samples = int(sample_rate * min_duration_ms / 1000)
        is_silent = np.abs(audio) < threshold
        
        silent_regions = []
        start = None
        
        for i, silent in enumerate(is_silent):
            if silent and start is None:
                start = i
            elif not silent and start is not None:
                duration = (i - start) / sample_rate * 1000
                if duration >= min_duration_ms:
                    silent_regions.append((start / sample_rate, i / sample_rate))
                start = None
        
        if start is not None:
            duration = (len(audio) - start) / sample_rate * 1000
            if duration >= min_duration_ms:
                silent_regions.append((start / sample_rate, len(audio) / sample_rate))
        
        return silent_regions
    
    @staticmethod
    def calculate_rms(audio: np.ndarray) -> float:
        """Calculate RMS level"""
        return float(np.sqrt(np.mean(audio ** 2)))
    
    @staticmethod
    def calculate_loudness(audio: np.ndarray) -> float:
        """Calculate perceived loudness in LUFS (simplified)"""
        rms = AudioUtils.calculate_rms(audio)
        if rms > 0:
            return float(20 * np.log10(rms))
        return -100.0