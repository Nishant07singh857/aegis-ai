# backend/app/vision/capture/__init__.py
"""
AEGIS AI - Vision Capture Module
Frame capture, device management, ADB control,
feed health monitoring, and resolution scaling.
"""

from .frame_grabber import (
    FrameGrabber,
    FrameSource,
    CapturedFrame,
    SourceState,
    StreamConfig,
)
from .device_manager import DeviceManager, SpectatorDevice, DeviceStatus
from .adb_controller import ADBController, ADBCommand
from .feed_health_monitor import FeedHealthMonitor, FeedHealth, HealthStatus
from .resolution_scaler import ResolutionScaler, ScalerConfig
from .config import CaptureConfig, load_capture_config, parse_roi
from .pipeline import FramePipeline, PipelineResult, DetectorOutcome
from .scrcpy_capture import ScrcpyCapture

__all__ = [
    "FrameGrabber",
    "FrameSource",
    "CapturedFrame",
    "SourceState",
    "StreamConfig",
    "DeviceManager",
    "SpectatorDevice",
    "DeviceStatus",
    "ADBController",
    "ADBCommand",
    "FeedHealthMonitor",
    "FeedHealth",
    "HealthStatus",
    "ResolutionScaler",
    "ScalerConfig",
    "CaptureConfig",
    "load_capture_config",
    "parse_roi",
    "FramePipeline",
    "PipelineResult",
    "DetectorOutcome",
    "ScrcpyCapture",
]