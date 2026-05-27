# backend/app/vision/inference/inference_engine.py
"""
AEGIS AI - Inference Engine
Central inference engine that coordinates all vision processing.
Manages workers, models, batching, and GPU resources.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np

from .worker import WorkerPool, InferenceTask, TaskPriority
from .gpu_manager import GPUManager
from .batch_processor import BatchProcessor, BatchOptimizer
from .model_loader import ModelLoader, ModelRegistry, ModelInfo, ModelFormat

logger = structlog.get_logger("aegis.vision.engine")


class EngineState(str, Enum):
    """Inference engine states"""
    INITIALIZING = "initializing"
    RUNNING = "running"
    PAUSED = "paused"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    ERROR = "error"


@dataclass
class EngineConfig:
    """Inference engine configuration"""
    # Workers
    num_workers: int = 2
    gpu_ids: List[int] = field(default_factory=lambda: [0])
    
    # Models
    preload_models: List[str] = field(default_factory=list)
    model_paths: Dict[str, str] = field(default_factory=dict)
    
    # Batching
    enable_batching: bool = True
    max_batch_size: int = 8
    max_wait_time_ms: float = 50.0
    
    # GPU
    enable_fp16: bool = True
    enable_tensorrt: bool = False
    max_gpu_memory_pct: float = 0.85
    
    # Priority
    high_priority_feeds: List[str] = field(default_factory=list)
    
    # Performance
    target_fps: int = 30
    max_latency_ms: float = 100.0


class InferenceEngine:
    """
    Central inference engine for all vision processing.
    
    Coordinates:
    - GPU worker pool
    - Model loading/unloading
    - Batch processing
    - Task scheduling
    - Result collection
    
    This is the main entry point for all computer vision inference.
    """
    
    def __init__(self, config: Optional[EngineConfig] = None):
        self.config = config or EngineConfig()
        self.state = EngineState.INITIALIZING
        
        # Core components
        self.gpu_manager = GPUManager(self.config.gpu_ids)
        self.registry = ModelRegistry()
        self.model_loader = ModelLoader(self.registry, self.gpu_manager)
        self.batch_processor = BatchProcessor()
        self.batch_optimizer = BatchOptimizer()
        self.worker_pool: Optional[WorkerPool] = None
        
        # Task tracking
        self._task_counter = 0
        self._results: Dict[str, Any] = {}
        
        # Statistics
        self._total_inferences = 0
        self._total_errors = 0
        self._avg_latency_ms = 0.0
        
        self._log = logger.bind(component="inference_engine")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LIFECYCLE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def initialize(self) -> None:
        """Initialize the inference engine"""
        
        self._log.info("Initializing inference engine...")
        
        # Register default models
        self._register_default_models()
        
        # Start GPU monitoring
        self.gpu_manager.start_monitoring()
        
        # Create worker pool
        model_configs = self._build_model_configs()
        self.worker_pool = WorkerPool(
            num_workers=self.config.num_workers,
            gpu_ids=self.config.gpu_ids,
            model_configs=model_configs,
        )
        
        await self.worker_pool.start()
        
        # Preload models
        for model_name in self.config.preload_models:
            await self.model_loader.load_model(model_name)
        
        self.state = EngineState.RUNNING
        
        self._log.info(
            f"Inference engine initialized: {self.config.num_workers} workers "
            f"on GPUs {self.config.gpu_ids}"
        )
    
    async def shutdown(self) -> None:
        """Shutdown the inference engine"""
        
        self.state = EngineState.SHUTTING_DOWN
        
        # Stop worker pool
        if self.worker_pool:
            await self.worker_pool.stop()
        
        # Unload all models
        for model_name in self.model_loader.get_loaded_models():
            await self.model_loader.unload_model(model_name)
        
        # Stop GPU monitoring
        self.gpu_manager.stop_monitoring()
        
        self.state = EngineState.STOPPED
        self._log.info("Inference engine shut down")
    
    def _register_default_models(self) -> None:
        """Register default models"""
        
        models = [
            ModelInfo(
                model_name="yolov8n",
                model_type="yolo",
                format=ModelFormat.PYTORCH,
                model_path=self.config.model_paths.get("yolo", "models/yolov8n.pt"),
                estimated_memory_mb=200,
                input_size=(640, 640),
                num_classes=80,
                use_fp16=self.config.enable_fp16,
            ),
            ModelInfo(
                model_name="paddleocr",
                model_type="ocr",
                format=ModelFormat.PADDLE,
                estimated_memory_mb=500,
                input_size=(1920, 1080),
            ),
            ModelInfo(
                model_name="yolov8m_bgmi",
                model_type="yolo",
                format=ModelFormat.PYTORCH,
                model_path=self.config.model_paths.get("yolo_bgmi", "models/yolov8m_bgmi.pt"),
                estimated_memory_mb=800,
                input_size=(640, 640),
                use_fp16=self.config.enable_fp16,
            ),
        ]
        
        for model in models:
            self.registry.register(model)
    
    def _build_model_configs(self) -> Dict[str, Any]:
        """Build model configs for worker pool"""
        
        configs = {}
        
        for model_name in self.registry.list_models():
            info = self.registry.get(model_name)
            if info:
                configs[model_name] = {
                    "type": info.model_type,
                    "path": info.model_path,
                    "format": info.format.value,
                    "input_size": info.input_size,
                }
        
        return configs
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # INFERENCE API
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    async def run_inference(
        self,
        frame: np.ndarray,
        model_name: str,
        task_type: str = "detection",
        source_id: str = "",
        priority: TaskPriority = TaskPriority.MEDIUM,
        callback: Optional[callable] = None,
    ) -> Optional[str]:
        """
        Run inference on a frame.
        
        Args:
            frame: Input frame
            model_name: Model to use
            task_type: Type of inference
            source_id: Feed source identifier
            priority: Task priority
            callback: Optional callback on completion
            
        Returns:
            task_id for tracking, or None if failed
        """
        
        if self.state != EngineState.RUNNING:
            self._log.error("Engine not running")
            return None
        
        # Ensure model is loaded
        if not self.model_loader.is_loaded(model_name):
            model = await self.model_loader.load_model(model_name)
            if model is None:
                return None
        
        # Create task
        self._task_counter += 1
        task_id = f"inf_{int(time.time()*1000)}_{self._task_counter}"
        
        task = InferenceTask(
            task_id=task_id,
            task_type=task_type,
            priority=priority,
            frame=frame,
            frame_id=self._task_counter,
            source_id=source_id,
            frame_width=frame.shape[1] if frame is not None else 1920,
            frame_height=frame.shape[0] if frame is not None else 1080,
            model_name=model_name,
            model_input_size=self.registry.get(model_name).input_size if self.registry.get(model_name) else (640, 640),
            callback=callback,
        )
        
        # Submit to worker pool
        if self.worker_pool:
            await self.worker_pool.submit_task(task)
            self._total_inferences += 1
            return task_id
        
        return None
    
    async def run_batch_inference(
        self,
        frames: List[np.ndarray],
        model_name: str,
        task_type: str = "detection",
        source_ids: List[str] = None,
        priority: TaskPriority = TaskPriority.MEDIUM,
    ) -> List[str]:
        """Run inference on multiple frames"""
        
        task_ids = []
        
        for i, frame in enumerate(frames):
            source_id = source_ids[i] if source_ids and i < len(source_ids) else ""
            task_id = await self.run_inference(
                frame=frame,
                model_name=model_name,
                task_type=task_type,
                source_id=source_id,
                priority=priority,
            )
            if task_id:
                task_ids.append(task_id)
        
        return task_ids
    
    async def wait_for_result(self, task_id: str, timeout: float = 30.0) -> Optional[Any]:
        """Wait for an inference result"""
        
        if self.worker_pool:
            task = await self.worker_pool.wait_for_task(task_id, timeout)
            if task and task.is_complete:
                return task.result
        
        return None
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_stats(self) -> Dict[str, Any]:
        """Get inference engine statistics"""
        return {
            "state": self.state.value,
            "workers": self.worker_pool.get_stats() if self.worker_pool else None,
            "gpu": self.gpu_manager.get_stats(),
            "models": self.model_loader.get_stats(),
            "batch": self.batch_processor.get_stats(),
            "inferences": {
                "total": self._total_inferences,
                "errors": self._total_errors,
                "avg_latency_ms": round(self._avg_latency_ms, 2),
            },
        }
    
    def reset(self) -> None:
        """Reset inference engine statistics"""
        self._total_inferences = 0
        self._total_errors = 0
        self._avg_latency_ms = 0.0
        self._task_counter = 0
        self._log.info("Inference engine statistics reset")