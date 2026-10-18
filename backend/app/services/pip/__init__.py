"""AEGIS AI — PiP (picture-in-picture) service package."""

from .pip_service import (
    INSET_POSITIONS,
    SOURCES,
    WS_MESSAGE_TYPE,
    PiPService,
    PiPStateError,
    pip_service,
)

__all__ = [
    "INSET_POSITIONS",
    "SOURCES",
    "WS_MESSAGE_TYPE",
    "PiPService",
    "PiPStateError",
    "pip_service",
]
