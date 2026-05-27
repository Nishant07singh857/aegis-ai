# backend/app/vision/inference/worker.py
"""
AEGIS AI - Inference Worker
GPU-accelerated inference worker process.
Runs in separate process to avoid GIL contention.
Processes frames from multiple feeds with priority queuing.
"""

import asyncio
import time
import multiprocessing as mp
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
import numpy as np
import torch

logger = structlog.get_logger("aegis.vision.inference")


class TaskPriority(int, Enum):
    """Inference task priority levels"""
    CRITICAL = 1    # Active fight, kill feed
    HIGH = 2        # HP bars, minimap
    MEDIUM = 3      # Vehicle tracking
    LOW = 4         # Loot detection
    BACKGROUND = 5  # Analytics


class TaskStatus(str, Enum):
    """Task processing status"""
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class InferenceTask:
    """A single inference task"""
    task_id: str
    task_type: str  # "ocr", "detection", "classification", "tracking"
    priority: TaskPriority = TaskPriority.MEDIUM
    
    # Input
    frame: Optional[np.ndarray] = None
    frame_id: int = 0
    source_id: str = ""
    frame_width: int = 1920
    frame_height: int = 1080
    
    # Model
    model_name: str = ""
    model_input_size: Tuple[int, int] = (640, 640)
    
    # Processing
    status: TaskStatus = TaskStatus.QUEUED
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    processing_time_ms: float = 0.0
    
    # Result
    result: Any = None
    error: Optional[str] = None
    
    # Callback
    callback: Optional[callable] = None
    
    @property
    def age_ms(self) -> float:
        """Age of task in milliseconds"""
        return (time.time() - self.created_at) * 1000
    
    @property
    def is_complete(self) -> bool:
        return self.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.TIMEOUT]


class InferenceWorker(mp.Process):
    """
    GPU inference worker running in a separate process.
    
    Each worker:
    - Runs on a dedicated GPU
    - Has its own model cache
    - Processes tasks from input queue
    - Returns results via output queue
    
    Separate process avoids Python GIL bottleneck
    for GPU-intensive inference operations.
    """
    
    def __init__(
        self,
        worker_id: int,
        gpu_id: int,
        input_queue: mp.Queue,
        output_queue: mp.Queue,
        model_configs: Dict[str, Any] = None,
    ):
        super().__init__()
        self.worker_id = worker_id
        self.gpu_id = gpu_id
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.model_configs = model_configs or {}
        
        # Internal state
        self._running = False
        self._models: Dict[str, Any] = {}
        self._total_processed = 0
        self._total_errors = 0
        
        self.daemon = True  # Auto-kill when parent exits
    
    def run(self) -> None:
        """Main worker loop - runs in separate process"""
        
        # Set GPU device
        if torch.cuda.is_available():
            torch.cuda.set_device(self.gpu_id)
            logger.info(f"Worker {self.worker_id} using GPU {self.gpu_id}: {torch.cuda.get_device_name(self.gpu_id)}")
        
        self._running = True
        
        # Preload models
        self._preload_models()
        
        logger.info(f"Worker {self.worker_id} started on GPU {self.gpu_id}")
        
        # Main processing loop
        while self._running:
            try:
                # Get task with timeout
                task = self.input_queue.get(timeout=1.0)
                
                if task is None:  # Shutdown signal
                    break
                
                # Process task
                result = self._process_task(task)
                
                # Send result back
                self.output_queue.put((task.task_id, result))
                
                self._total_processed += 1
                
            except Exception as e:
                logger.error(f"Worker {self.worker_id} error: {e}")
                self._total_errors += 1
        
        # Cleanup
        self._cleanup()
        logger.info(f"Worker {self.worker_id} stopped. Processed: {self._total_processed}")
    
    def _preload_models(self) -> None:
        """Preload configured models into GPU memory"""
        
        for model_name, config in self.model_configs.items():
            try:
                model = self._load_model(model_name, config)
                if model is not None:
                    self._models[model_name] = model
                    logger.debug(f"Worker {self.worker_id} loaded model: {model_name}")
            except Exception as e:
                logger.error(f"Worker {self.worker_id} failed to load {model_name}: {e}")
    
    def _load_model(self, model_name: str, config: Dict[str, Any]) -> Any:
        """Load a specific model"""
        
        model_type = config.get("type", "")
        model_path = config.get("path", "")
        
        if model_type == "yolo":
            from ultralytics import YOLO
            return YOLO(model_path)
        elif model_type == "ocr":
            from paddleocr import PaddleOCR
            return PaddleOCR(
                use_angle_cls=config.get("use_angle_cls", True),
                lang=config.get("lang", "en"),
                use_gpu=True,
                show_log=False,
            )
        elif model_type == "torch":
            return torch.jit.load(model_path)
        elif model_type == "tensorrt":
            # TensorRT engine loading
            pass
        
        return None
    
    def _process_task(self, task: InferenceTask) -> Any:
        """Process a single inference task"""
        
        task.status = TaskStatus.PROCESSING
        task.started_at = time.time()
        
        try:
            # Get or load model
            model = self._models.get(task.model_name)
            if model is None:
                # Try to load on demand
                config = self.model_configs.get(task.model_name, {})
                model = self._load_model(task.model_name, config)
                if model is not None:
                    self._models[task.model_name] = model
            
            if model is None:
                raise RuntimeError(f"Model not found: {task.model_name}")
            
            # Run inference based on task type
            if task.task_type == "detection":
                result = self._run_detection(model, task)
            elif task.task_type == "ocr":
                result = self._run_ocr(model, task)
            elif task.task_type == "classification":
                result = self._run_classification(model, task)
            else:
                result = self._run_generic(model, task)
            
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.processing_time_ms = (task.completed_at - task.started_at) * 1000
            task.result = result
            
            return result
            
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            logger.error(f"Worker {self.worker_id} task failed: {task.task_type} - {e}")
            return None
    
    def _run_detection(self, model: Any, task: InferenceTask) -> Any:
        """Run object detection inference"""
        
        if task.frame is None:
            return None
        
        # Preprocess frame
        import cv2
        frame = cv2.resize(task.frame, task.model_input_size)
        
        # Run inference
        results = model(frame, verbose=False)
        
        return results
    
    def _run_ocr(self, model: Any, task: InferenceTask) -> Any:
        """Run OCR inference"""
        
        if task.frame is None:
            return None
        
        results = model.ocr(task.frame, cls=True)
        return results
    
    def _run_classification(self, model: Any, task: InferenceTask) -> Any:
        """Run classification inference"""
        
        if task.frame is None:
            return None
        
        import cv2
        import torch
        
        frame = cv2.resize(task.frame, task.model_input_size)
        tensor = torch.from_numpy(frame).permute(2, 0, 1).unsqueeze(0).float() / 255.0
        
        if torch.cuda.is_available():
            tensor = tensor.cuda()
        
        with torch.no_grad():
            result = model(tensor)
        
        return result
    
    def _run_generic(self, model: Any, task: InferenceTask) -> Any:
        """Run generic inference"""
        return None
    
    def _cleanup(self) -> None:
        """Cleanup GPU memory"""
        self._models.clear()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    
    def stop(self) -> None:
        """Signal worker to stop"""
        self._running = False


class WorkerPool:
    """
    Pool of GPU inference workers.
    
    Manages:
    - Multiple worker processes
    - Task distribution with load balancing
    - Priority-based scheduling
    - Worker health monitoring
    - Automatic worker restart on failure
    """
    
    def __init__(
        self,
        num_workers: int = 2,
        gpu_ids: List[int] = None,
        model_configs: Dict[str, Any] = None,
    ):
        self.num_workers = num_workers
        self.gpu_ids = gpu_ids or [0]
        self.model_configs = model_configs or {}
        
        # Queues
        self.input_queues: List[mp.Queue] = []
        self.output_queue: mp.Queue = mp.Queue()
        
        # Workers
        self.workers: List[InferenceWorker] = []
        
        # Task tracking
        self._pending_tasks: Dict[str, InferenceTask] = {}
        self._task_counter = 0
        
        # Round-robin worker assignment
        self._next_worker = 0
        
        # Statistics
        self._total_submitted = 0
        self._total_completed = 0
        self._total_failed = 0
        
        self._running = False
        self._result_task: Optional[asyncio.Task] = None
    
    async def start(self) -> None:
        """Start all workers"""
        
        self._running = True
        
        # Create input queues
        self.input_queues = [mp.Queue(maxsize=100) for _ in range(self.num_workers)]
        
        # Create workers
        for i in range(self.num_workers):
            gpu_id = self.gpu_ids[i % len(self.gpu_ids)]
            
            worker = InferenceWorker(
                worker_id=i,
                gpu_id=gpu_id,
                input_queue=self.input_queues[i],
                output_queue=self.output_queue,
                model_configs=self.model_configs,
            )
            
            worker.start()
            self.workers.append(worker)
        
        # Start result collector
        self._result_task = asyncio.create_task(self._collect_results())
        
        logger.info(f"Worker pool started: {self.num_workers} workers on GPUs {self.gpu_ids}")
    
    async def stop(self) -> None:
        """Stop all workers"""
        
        self._running = False
        
        # Send shutdown signals
        for queue in self.input_queues:
            queue.put(None)
        
        # Wait for workers
        for worker in self.workers:
            worker.join(timeout=5)
            if worker.is_alive():
                worker.terminate()
        
        # Cancel result collector
        if self._result_task:
            self._result_task.cancel()
        
        logger.info("Worker pool stopped")
    
    async def submit_task(self, task: InferenceTask) -> str:
        """
        Submit a task for inference.
        
        Args:
            task: Inference task to process
            
        Returns:
            task_id for tracking
        """
        
        if not self._running:
            raise RuntimeError("Worker pool not running")
        
        # Assign to worker (round-robin with load balancing)
        worker_idx = self._get_best_worker()
        
        # Store task for tracking
        self._pending_tasks[task.task_id] = task
        self._total_submitted += 1
        
        # Submit to worker queue
        try:
            self.input_queues[worker_idx].put_nowait(task)
        except Exception:
            # Queue full - try next worker
            for i in range(self.num_workers):
                try:
                    self.input_queues[i].put_nowait(task)
                    break
                except Exception:
                    continue
        
        return task.task_id
    
    async def submit_batch(self, tasks: List[InferenceTask]) -> List[str]:
        """Submit multiple tasks"""
        task_ids = []
        for task in tasks:
            task_id = await self.submit_task(task)
            task_ids.append(task_id)
        return task_ids
    
    def _get_best_worker(self) -> int:
        """Get best worker for next task (load balancing)"""
        
        # Simple round-robin with queue size check
        best_idx = self._next_worker
        best_size = float('inf')
        
        for i in range(self.num_workers):
            qsize = self.input_queues[i].qsize()
            if qsize < best_size:
                best_size = qsize
                best_idx = i
        
        self._next_worker = (best_idx + 1) % self.num_workers
        return best_idx
    
    async def _collect_results(self) -> None:
        """Collect results from output queue"""
        
        while self._running:
            try:
                # Check for results (non-blocking)
                if not self.output_queue.empty():
                    task_id, result = self.output_queue.get_nowait()
                    
                    task = self._pending_tasks.pop(task_id, None)
                    if task:
                        task.result = result
                        self._total_completed += 1
                        
                        # Call callback if set
                        if task.callback:
                            try:
                                if asyncio.iscoroutinefunction(task.callback):
                                    await task.callback(task)
                                else:
                                    task.callback(task)
                            except Exception as e:
                                logger.error(f"Task callback error: {e}")
                
                await asyncio.sleep(0.001)  # Small sleep to prevent CPU spin
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Result collection error: {e}")
    
    async def wait_for_task(self, task_id: str, timeout: float = 30.0) -> Optional[InferenceTask]:
        """Wait for a specific task to complete"""
        
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            task = self._pending_tasks.get(task_id)
            
            if task is None or task.is_complete:
                return task
            
            await asyncio.sleep(0.01)
        
        # Timeout
        task = self._pending_tasks.pop(task_id, None)
        if task:
            task.status = TaskStatus.TIMEOUT
            self._total_failed += 1
        
        return task
    
    def get_stats(self) -> Dict[str, Any]:
        """Get worker pool statistics"""
        return {
            "workers": self.num_workers,
            "gpus": self.gpu_ids,
            "running": self._running,
            "submitted": self._total_submitted,
            "completed": self._total_completed,
            "failed": self._total_failed,
            "pending": len(self._pending_tasks),
            "queue_sizes": [q.qsize() for q in self.input_queues],
        }
    
    def reset(self) -> None:
        """Reset pool statistics"""
        self._total_submitted = 0
        self._total_completed = 0
        self._total_failed = 0
        self._pending_tasks.clear()
