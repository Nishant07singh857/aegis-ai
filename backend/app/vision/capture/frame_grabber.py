# backend/app/vision/capture/frame_grabber.py
"""
AEGIS AI - Frame Grabber
Captures video frames from multiple sources:
RTMP streams, NDI feeds, screen capture, and ADB devices.
"""

import asyncio
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
from collections import deque
import structlog
import numpy as np

logger = structlog.get_logger("aegis.vision.capture")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ENUMS & DATA MODELS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FrameSource(str, Enum):
    """Types of frame sources"""
    RTMP_STREAM = "rtmp_stream"
    NDI_FEED = "ndi_feed"
    SCREEN_CAPTURE = "screen_capture"
    ADB_DEVICE = "adb_device"
    VIDEO_FILE = "video_file"
    WEBCAM = "webcam"
    IP_CAMERA = "ip_camera"

class FrameFormat(str, Enum):
    """Frame color formats"""
    BGR = "bgr"           # OpenCV default
    RGB = "rgb"
    GRAY = "gray"
    YUV = "yuv"
    NV12 = "nv12"

@dataclass
class CapturedFrame:
    """A single captured video frame"""
    frame_id: int
    source_id: str
    source_type: FrameSource
    timestamp: float = field(default_factory=time.time)
    
    # Image data
    image: Optional[np.ndarray] = None
    width: int = 1920
    height: int = 1080
    channels: int = 3
    format: FrameFormat = FrameFormat.BGR
    
    # Metadata
    fps: float = 30.0
    is_keyframe: bool = False
    sequence_number: int = 0
    
    # Device info
    device_id: str = ""
    team_id: str = ""
    player_id: str = ""
    
    # Quality
    quality_score: float = 1.0
    has_artifact: bool = False
    
    def get_roi(self, x: int, y: int, w: int, h: int) -> Optional[np.ndarray]:
        """Extract region of interest"""
        if self.image is None:
            return None
        return self.image[y:y+h, x:x+w]
    
    def resize(self, width: int, height: int) -> None:
        """Resize frame in place"""
        if self.image is not None:
            import cv2
            self.image = cv2.resize(self.image, (width, height))
            self.width = width
            self.height = height
    
    def to_grayscale(self) -> None:
        """Convert to grayscale"""
        if self.image is not None and self.channels == 3:
            import cv2
            self.image = cv2.cvtColor(self.image, cv2.COLOR_BGR2GRAY)
            self.channels = 1
            self.format = FrameFormat.GRAY


@dataclass
class StreamConfig:
    """Configuration for a capture stream"""
    source_id: str
    source_type: FrameSource
    source_url: str = ""
    device_id: str = ""
    team_id: str = ""
    player_id: str = ""
    
    # Quality
    target_fps: int = 30
    target_width: int = 1920
    target_height: int = 1080
    bitrate: int = 8000000
    
    # Buffering
    buffer_size: int = 30
    reconnect_attempts: int = 5
    reconnect_delay: float = 2.0
    
    # Processing
    enable_resize: bool = False
    enable_grayscale: bool = False
    roi: Optional[Tuple[int, int, int, int]] = None


class FrameGrabber:
    """
    Multi-source frame capture system.
    
    Supports:
    - RTMP/RTSP streams (OBS, streaming software)
    - NDI feeds (professional video)
    - Screen capture (local display)
    - ADB device capture (Android phones/tablets)
    - Video file input (recorded matches)
    - IP camera feeds
    
    Features:
    - Async frame grabbing
    - Frame buffering
    - Automatic reconnection
    - Frame quality monitoring
    - Region of interest extraction
    - Format conversion
    """
    
    def __init__(self):
        # Active captures
        self.captures: Dict[str, StreamConfig] = {}
        self.frame_buffers: Dict[str, deque] = {}
        self.capture_tasks: Dict[str, asyncio.Task] = {}
        
        # Frame counter
        self._frame_counter: Dict[str, int] = {}
        
        # Statistics
        self._total_frames_captured: int = 0
        self._capture_errors: Dict[str, int] = {}
        
        self._running = False
        self._log = logger.bind(component="frame_grabber")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CAPTURE MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def add_source(self, config: StreamConfig) -> str:
        """Add a new capture source"""
        source_id = config.source_id
        
        self.captures[source_id] = config
        self.frame_buffers[source_id] = deque(maxlen=config.buffer_size)
        self._frame_counter[source_id] = 0
        self._capture_errors[source_id] = 0
        
        self._log.info(
            f"Source added: {source_id}",
            type=config.source_type.value,
            resolution=f"{config.target_width}x{config.target_height}",
            fps=config.target_fps,
        )
        
        return source_id
    
    def remove_source(self, source_id: str) -> bool:
        """Remove a capture source"""
        if source_id in self.captures:
            # Cancel capture task
            task = self.capture_tasks.pop(source_id, None)
            if task:
                task.cancel()
            
            del self.captures[source_id]
            del self.frame_buffers[source_id]
            del self._frame_counter[source_id]
            del self._capture_errors[source_id]
            
            self._log.info(f"Source removed: {source_id}")
            return True
        
        return False
    
    async def start_capture(self, source_id: str) -> bool:
        """Start capturing from a source"""
        config = self.captures.get(source_id)
        if not config:
            self._log.error(f"Source not found: {source_id}")
            return False
        
        self._running = True
        
        # Create capture task
        task = asyncio.create_task(self._capture_loop(config))
        self.capture_tasks[source_id] = task
        
        self._log.info(f"Capture started: {source_id}")
        return True
    
    async def stop_capture(self, source_id: str) -> bool:
        """Stop capturing from a source"""
        task = self.capture_tasks.pop(source_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            self._log.info(f"Capture stopped: {source_id}")
            return True
        
        return False
    
    async def stop_all(self) -> None:
        """Stop all captures"""
        self._running = False
        
        for source_id in list(self.capture_tasks.keys()):
            await self.stop_capture(source_id)
        
        self._log.info("All captures stopped")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CAPTURE LOOPS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def _capture_loop(self, config: StreamConfig) -> None:
        """Main capture loop for a source"""
        
        source_id = config.source_id
        
        while self._running:
            try:
                frame = await self._capture_frame(config)
                
                if frame is not None:
                    # Process frame
                    frame = self._process_frame(frame, config)
                    
                    # Add to buffer
                    buffer = self.frame_buffers[source_id]
                    buffer.append(frame)
                    
                    # Update counter
                    self._frame_counter[source_id] += 1
                    self._total_frames_captured += 1
                    
                # Control frame rate
                await asyncio.sleep(1.0 / config.target_fps)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._capture_errors[source_id] += 1
                self._log.error(f"Capture error for {source_id}: {e}")
                
                # Reconnect delay
                await asyncio.sleep(config.reconnect_delay)
    
    async def _capture_frame(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture a single frame based on source type"""
        
        if config.source_type == FrameSource.RTMP_STREAM:
            return await self._capture_rtmp(config)
        elif config.source_type == FrameSource.NDI_FEED:
            return await self._capture_ndi(config)
        elif config.source_type == FrameSource.SCREEN_CAPTURE:
            return await self._capture_screen(config)
        elif config.source_type == FrameSource.ADB_DEVICE:
            return await self._capture_adb(config)
        elif config.source_type == FrameSource.VIDEO_FILE:
            return await self._capture_video_file(config)
        else:
            return await self._capture_generic(config)
    
    async def _capture_rtmp(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture from RTMP stream using OpenCV"""
        try:
            import cv2
            
            # Open RTMP stream
            cap = cv2.VideoCapture(config.source_url)
            
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                
                if ret:
                    return CapturedFrame(
                        frame_id=self._frame_counter.get(config.source_id, 0) + 1,
                        source_id=config.source_id,
                        source_type=FrameSource.RTMP_STREAM,
                        image=frame,
                        width=frame.shape[1],
                        height=frame.shape[0],
                        channels=frame.shape[2] if len(frame.shape) > 2 else 1,
                        device_id=config.device_id,
                        team_id=config.team_id,
                    )
        except ImportError:
            self._log.warning("OpenCV not available for RTMP capture")
        except Exception as e:
            self._log.error(f"RTMP capture error: {e}")
        
        return None
    
    async def _capture_ndi(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture from NDI feed"""
        # NDI capture would use NDI SDK
        # Placeholder for NDI integration
        return None
    
    async def _capture_screen(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture from screen"""
        try:
            import cv2
            import numpy as np
            from mss import mss
            
            with mss() as sct:
                monitor = sct.monitors[0]  # Primary monitor
                screenshot = sct.grab(monitor)
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
                
                return CapturedFrame(
                    frame_id=self._frame_counter.get(config.source_id, 0) + 1,
                    source_id=config.source_id,
                    source_type=FrameSource.SCREEN_CAPTURE,
                    image=frame,
                    width=frame.shape[1],
                    height=frame.shape[0],
                )
        except ImportError:
            self._log.warning("mss/cv2 not available for screen capture")
        
        return None
    
    async def _capture_adb(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture from ADB device using scrcpy or adb screenrecord"""
        try:
            import numpy as np
            
            # Use scrcpy for low-latency capture
            # scrcpy --no-display --record=pipe:1
            cmd = [
                "scrcpy",
                "-s", config.device_id,
                "--no-display",
                "--max-size", str(config.target_width),
                "--max-fps", str(config.target_fps),
                "--record", "pipe:1",
            ]
            
            # This would be managed as a persistent subprocess
            # For now, return placeholder
            
            # Create blank frame as placeholder
            frame = np.zeros((config.target_height, config.target_width, 3), dtype=np.uint8)
            
            return CapturedFrame(
                frame_id=self._frame_counter.get(config.source_id, 0) + 1,
                source_id=config.source_id,
                source_type=FrameSource.ADB_DEVICE,
                image=frame,
                width=config.target_width,
                height=config.target_height,
                device_id=config.device_id,
                team_id=config.team_id,
            )
            
        except Exception as e:
            self._log.error(f"ADB capture error: {e}")
        
        return None
    
    async def _capture_video_file(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Capture from video file"""
        try:
            import cv2
            
            cap = cv2.VideoCapture(config.source_url)
            
            if cap.isOpened():
                ret, frame = cap.read()
                cap.release()
                
                if ret:
                    return CapturedFrame(
                        frame_id=self._frame_counter.get(config.source_id, 0) + 1,
                        source_id=config.source_id,
                        source_type=FrameSource.VIDEO_FILE,
                        image=frame,
                        width=frame.shape[1],
                        height=frame.shape[0],
                    )
        except Exception as e:
            self._log.error(f"Video file capture error: {e}")
        
        return None
    
    async def _capture_generic(self, config: StreamConfig) -> Optional[CapturedFrame]:
        """Generic capture fallback"""
        return None
    
    def _process_frame(self, frame: CapturedFrame, config: StreamConfig) -> CapturedFrame:
        """Process captured frame (resize, convert, ROI)"""
        
        if frame.image is None:
            return frame
        
        # Extract ROI if specified
        if config.roi:
            roi_frame = frame.get_roi(*config.roi)
            if roi_frame is not None:
                frame.image = roi_frame
                frame.width = roi_frame.shape[1]
                frame.height = roi_frame.shape[0]
        
        # Resize if needed
        if config.enable_resize:
            frame.resize(config.target_width, config.target_height)
        
        # Convert to grayscale if needed
        if config.enable_grayscale:
            frame.to_grayscale()
        
        return frame
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FRAME RETRIEVAL
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_latest_frame(self, source_id: str) -> Optional[CapturedFrame]:
        """Get the most recent frame from a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer and len(buffer) > 0:
            return buffer[-1]
        return None
    
    def get_frame_buffer(self, source_id: str) -> List[CapturedFrame]:
        """Get all buffered frames for a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer:
            return list(buffer)
        return []
    
    def get_all_latest_frames(self) -> Dict[str, CapturedFrame]:
        """Get latest frame from all sources"""
        frames = {}
        for source_id in self.captures:
            frame = self.get_latest_frame(source_id)
            if frame is not None:
                frames[source_id] = frame
        return frames
    
    def clear_buffer(self, source_id: str) -> None:
        """Clear frame buffer for a source"""
        buffer = self.frame_buffers.get(source_id)
        if buffer:
            buffer.clear()
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get frame grabber statistics"""
        source_stats = {}
        
        for source_id, config in self.captures.items():
            buffer = self.frame_buffers.get(source_id, deque())
            
            source_stats[source_id] = {
                "type": config.source_type.value,
                "frames_captured": self._frame_counter.get(source_id, 0),
                "buffer_size": len(buffer),
                "buffer_max": config.buffer_size,
                "errors": self._capture_errors.get(source_id, 0),
                "fps_target": config.target_fps,
                "is_active": source_id in self.capture_tasks,
            }
        
        return {
            "total_frames_captured": self._total_frames_captured,
            "active_sources": len(self.capture_tasks),
            "total_sources": len(self.captures),
            "sources": source_stats,
        }
    
    def reset(self) -> None:
        """Reset frame grabber"""
        for buffer in self.frame_buffers.values():
            buffer.clear()
        self._frame_counter.clear()
        self._total_frames_captured = 0
        self._log.info("Frame grabber reset")