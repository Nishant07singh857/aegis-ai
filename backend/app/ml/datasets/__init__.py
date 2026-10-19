# backend/app/ml/datasets/__init__.py
"""AEGIS AI - ML Training Data Flywheel (Phase 15, W1).

Every real match the user records becomes labeled training data:

- ``recorder``: captures the observer screen to a timestamped mp4 while a
  match is played, alongside a ``timestamps.csv`` (frame_idx, epoch_ts).
- ``auto_labeler``: joins that recording with spotter/feed events
  (``NormalizedEvent``-shaped JSONL) by timestamp and emits a versioned
  labeled dataset: ``datasets/v1/`` with ``frames/`` (sampled JPEGs around
  each event) plus ``manifest.csv``.

Labels come ONLY from real logged events — the labeler never invents them.

Submodules are imported lazily (PEP 562), matching ``app.ml``'s convention:
importing this package has no side effects, and ``python -m
app.ml.datasets.recorder`` runs without the "module already in sys.modules"
warning. Heavy imports (cv2) are guarded locally inside the submodules.
"""

_LAZY_EXPORTS = {
    # auto_labeler
    "LABEL_EVENT_TYPES": ".auto_labeler",
    "DatasetReport": ".auto_labeler",
    "build_dataset": ".auto_labeler",
    "load_events": ".auto_labeler",
    "load_timestamps": ".auto_labeler",
    # recorder
    "RecordingResult": ".recorder",
    "iter_mss_frames": ".recorder",
    "iter_test_frames": ".recorder",
    "record_match": ".recorder",
}


def __getattr__(name: str):
    """Lazily import heavy submodules on first attribute access."""
    if name in _LAZY_EXPORTS:
        import importlib

        module = importlib.import_module(_LAZY_EXPORTS[name], __name__)
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = sorted(_LAZY_EXPORTS)
