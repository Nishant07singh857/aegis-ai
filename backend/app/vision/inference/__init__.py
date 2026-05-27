# backend/app/vision/inference/__init__.py
"""
AEGIS AI - Vision Inference Engine
GPU-accelerated inference system with worker processes,
GPU memory management, batch processing, and model lifecycle.
"""

from .worker import InferenceWorker, WorkerPool, InferenceTask
from .gpu_manager import GPUManager, GPUDevice, MemoryAllocation
from .batch_processor import BatchProcessor, BatchOptimizer
from .model_loader import ModelLoader, ModelRegistry, ModelInfo
from .inference_engine import InferenceEngine, EngineConfig

__all__ = [
    "InferenceWorker",
    "WorkerPool", 
    "InferenceTask",
    "GPUManager",
    "GPUDevice",
    "MemoryAllocation",
    "BatchProcessor",
    "BatchOptimizer",
    "ModelLoader",
    "ModelRegistry",
    "ModelInfo",
    "InferenceEngine",
    "EngineConfig",
]