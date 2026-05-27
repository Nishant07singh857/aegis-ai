# backend/app/broadcast/recording_manager.py
"""
AEGIS AI - Recording Manager
Manages broadcast recording, streaming, and VOD creation.
Handles recording start/stop, format settings, and file management.
"""

import os
import time
from typing import Dict, Optional, Any
from dataclasses import dataclass
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.broadcast.recording")


class RecordingStatus(str, Enum):
    """Recording status"""
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    PAUSED = "paused"
    STOPPING = "stopping"
    FINALIZING = "finalizing"
    COMPLETE = "complete"
    ERROR = "error"


class RecordingFormat(str, Enum):
    """Recording output formats"""
    MP4 = "mp4"
    MKV = "mkv"
    FLV = "flv"
    MOV = "mov"
    TS = "ts"


@dataclass
class Recording:
    """A recording session"""
    recording_id: str
    filename: str = ""
    format: RecordingFormat = RecordingFormat.MKV
    status: RecordingStatus = RecordingStatus.IDLE
    
    # Path
    output_dir: str = "recordings"
    full_path: str = ""
    
    # Timing
    started_at: Optional[float] = None
    stopped_at: Optional[float] = None
    duration_seconds: float = 0.0
    
    # Size
    file_size_mb: float = 0.0
    
    # Settings
    quality: str = "high"  # high, medium, low
    encoder: str = "nvenc"  # nvenc, x264, amf
    
    # Metadata
    match_id: str = ""
    tournament_name: str = ""
    title: str = ""


class RecordingManager:
    """
    Broadcast recording and VOD management.
    
    Features:
    - Start/stop recording
    - Pause/resume recording
    - Multiple output formats
    - Automatic file naming
    - Recording splitting
    - VOD metadata management
    """
    
    def __init__(self, obs_client: Any):
        self.obs = obs_client
        self.recordings: Dict[str, Recording] = {}
        self.active_recording: Optional[Recording] = None
        self.output_dir: str = "recordings"
        
        # Statistics
        self._total_recordings: int = 0
        self._total_duration: float = 0.0
        
        # Ensure output directory exists
        os.makedirs(self.output_dir, exist_ok=True)
        
        self._log = logger.bind(component="recording_manager")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # RECORDING CONTROL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def start_recording(
        self,
        match_id: str = "",
        tournament_name: str = "",
        quality: str = "high",
    ) -> Optional[str]:
        """Start a new recording"""
        
        if self.active_recording:
            self._log.warning("Recording already in progress")
            return None
        
        # Generate filename
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"aegis_{tournament_name}_{timestamp}" if tournament_name else f"aegis_{timestamp}"
        
        recording = Recording(
            recording_id=f"rec_{int(time.time()*1000)}",
            filename=filename,
            format=RecordingFormat.MKV,
            status=RecordingStatus.STARTING,
            output_dir=self.output_dir,
            full_path=os.path.join(self.output_dir, f"{filename}.mkv"),
            match_id=match_id,
            tournament_name=tournament_name,
            quality=quality,
        )
        
        # Start OBS recording
        response = await self.obs.start_recording()
        
        if response:
            recording.status = RecordingStatus.RECORDING
            recording.started_at = time.time()
            
            self.active_recording = recording
            self.recordings[recording.recording_id] = recording
            self._total_recordings += 1
            
            self._log.info(f"Recording started: {recording.filename}")
            return recording.recording_id
        
        recording.status = RecordingStatus.ERROR
        return None
    
    async def stop_recording(self) -> bool:
        """Stop current recording"""
        
        if not self.active_recording:
            return False
        
        self.active_recording.status = RecordingStatus.STOPPING
        
        response = await self.obs.stop_recording()
        
        if response:
            self.active_recording.status = RecordingStatus.COMPLETE
            self.active_recording.stopped_at = time.time()
            self.active_recording.duration_seconds = (
                self.active_recording.stopped_at - self.active_recording.started_at
            )
            
            self._total_duration += self.active_recording.duration_seconds
            
            # Update file size
            if os.path.exists(self.active_recording.full_path):
                self.active_recording.file_size_mb = (
                    os.path.getsize(self.active_recording.full_path) / (1024 * 1024)
                )
            
            self._log.info(
                f"Recording stopped: {self.active_recording.filename} "
                f"({self.active_recording.duration_seconds:.0f}s, "
                f"{self.active_recording.file_size_mb:.1f}MB)"
            )
            
            self.active_recording = None
            return True
        
        return False
    
    async def pause_recording(self) -> bool:
        """Pause current recording"""
        if not self.active_recording:
            return False
        
        response = await self.obs.send_command("PauseRecord")
        
        if response.success:
            self.active_recording.status = RecordingStatus.PAUSED
            return True
        return False
    
    async def resume_recording(self) -> bool:
        """Resume paused recording"""
        if not self.active_recording:
            return False
        
        response = await self.obs.send_command("ResumeRecord")
        
        if response.success:
            self.active_recording.status = RecordingStatus.RECORDING
            return True
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SETTINGS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def set_quality(self, quality: str) -> bool:
        """Set recording quality preset"""
        
        quality_settings = {
            "high": {
                "recQuality": "High",
                "recFormat": "mkv",
                "encoder": "nvenc_hevc",
            },
            "medium": {
                "recQuality": "Medium",
                "recFormat": "mkv",
                "encoder": "nvenc_hevc",
            },
            "low": {
                "recQuality": "Low",
                "recFormat": "mp4",
                "encoder": "x264",
            },
        }
        
        settings = quality_settings.get(quality, quality_settings["high"])
        
        response = await self.obs.send_command("SetRecordDirectory", {
            "recordDirectory": self.output_dir,
        })
        
        return response.success
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_recording(self, recording_id: str) -> Optional[Recording]:
        """Get recording by ID"""
        return self.recordings.get(recording_id)
    
    def get_active_recording(self) -> Optional[Recording]:
        """Get currently active recording"""
        return self.active_recording
    
    def is_recording(self) -> bool:
        """Check if currently recording"""
        return self.active_recording is not None and self.active_recording.status == RecordingStatus.RECORDING
    
    def get_stats(self) -> Dict[str, Any]:
        """Get recording manager statistics"""
        return {
            "active_recording": self.active_recording.filename if self.active_recording else None,
            "is_recording": self.is_recording(),
            "total_recordings": self._total_recordings,
            "total_duration_hours": round(self._total_duration / 3600, 2),
            "output_dir": self.output_dir,
            "recent_recordings": [
                {
                    "filename": r.filename,
                    "duration": round(r.duration_seconds, 0),
                    "size_mb": round(r.file_size_mb, 1),
                    "status": r.status.value,
                }
                for r in list(self.recordings.values())[-5:]
            ],
        }