# backend/app/vision/__init__.py
"""
AEGIS AI - Vision Pipeline

Real-time computer vision for match observation:

- ``capture``: frame acquisition (ADB device, feeds, health monitoring).
- ``detectors``: per-frame detectors — kill feed OCR, minimap reader,
  player tracker, zone/vehicle/weapon/loot/knock/heal/alive-counter
  detectors.
- ``inference``: GPU management, batch processing, model loading.
- ``ingestion_manager``: routes frames into the detection pipeline.

Heavy optional dependencies (torch, cv2, ultralytics, paddleocr) are
NOT imported here. Submodules guard their own imports, so importing
``app.vision`` always works; importing a specific submodule raises
the real ImportError only if its own dependencies are missing.
"""

from typing import TYPE_CHECKING

# Submodule names that are safe to resolve lazily. They are imported
# on first attribute access so that ``import app.vision`` never pulls
# in torch/cv2 transitively.
_LAZY_SUBMODULES = frozenset({
    "capture",
    "detectors",
    "inference",
    "ingestion_manager",
    "post_processors",
})

if TYPE_CHECKING:  # pragma: no cover - type checking only
    from . import capture, detectors, inference  # noqa: F401


def __getattr__(name: str):
    """
    Lazily import vision submodules on attribute access.

    Raises:
        AttributeError: For unknown names.
        ImportError: If the submodule exists but its own heavy
            dependencies (torch/cv2/...) are missing — the original
            ImportError propagates unchanged.
    """
    if name in _LAZY_SUBMODULES:
        import importlib

        module = importlib.import_module(f"{__name__}.{name}")
        globals()[name] = module
        return module
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | _LAZY_SUBMODULES)


__all__ = sorted(_LAZY_SUBMODULES)
