# backend/app/vision/capture/__init__.py
"""
AEGIS AI - Vision Capture Module
Frame capture, device management, ADB control,
feed health monitoring, and resolution scaling.
"""

from .frame_grabber import FrameGrabber, FrameSource, CapturedFrame
from .device_manager import DeviceManager, SpectatorDevice, DeviceStatus
from .adb_controller import ADBController, ADBCommand
from .feed_health_monitor import FeedHealthMonitor, FeedHealth, HealthStatus
from .resolution_scaler import ResolutionScaler, ScalerConfig

__all__ = [
    "FrameGrabber",
    "FrameSource", 
    "CapturedFrame",
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
]