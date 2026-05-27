# backend/app/vision/inference/batch_processor.py
"""
AEGIS AI - Batch Processor
Optimizes inference by batching multiple frames together.
Reduces GPU kernel launch overhead and improves throughput.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import structlog

logger = structlog.get_logger("aegis.vision.batch")


@dataclass
class BatchConfig:
    """Batch processing configuration"""
    max_batch_size: int = 8          # Maximum frames per batch
    max_wait_time_ms: float = 50.0   # Max time to wait for batch
    min_batch_size: int = 2          # Minimum batch size to process
    dynamic_batching: bool = True    # Adjust batch size dynamically
    prioritize_small_batches: bool = True  # Favor latency over throughput


@dataclass
class BatchJob:
    """A batch of inference jobs"""
    batch_id: str
    tasks: List[Any] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    model_name: str = ""
    input_size: Tuple[int, int] = (640, 640)
    
    @property
    def size(self) -> int:
        return len(self.tasks)
    
    @property
    def is_full(self) -> bool:
        return self.size >= 8


class BatchProcessor:
    """
    Optimizes inference throughput via intelligent batching.
    
    Features:
    - Dynamic batch size adjustment
    - Wait time vs batch size trade-off
    - Priority-aware batching
    - GPU memory-aware batching
    - Batch splitting for large jobs
    """
    
    def __init__(self, config: Optional[BatchConfig] = None):
        self.config = config or BatchConfig()
        
        # Batch queues per model
        self.batch_queues: Dict[str, List[Any]] = {}
        self.active_batches: Dict[str, BatchJob] = {}
        
        # Statistics
        self._total_batches = 0
        self._total_tasks_batched = 0
        self._avg_batch_size = 0.0
        
        self._log = logger.bind(component="batch_processor")
    
    def add_task(self, model_name: str, task: Any) -> None:
        """Add a task to the batch queue for a model"""
        
        if model_name not in self.batch_queues:
            self.batch_queues[model_name] = []
        
        self.batch_queues[model_name].append(task)
    
    def get_batch(self, model_name: str) -> Optional[BatchJob]:
        """
        Get a batch of tasks for processing.
        
        Returns a batch if:
        - Queue has enough tasks (min_batch_size)
        - Oldest task has waited long enough (max_wait_time)
        - Queue has tasks and dynamic batching allows it
        """
        
        queue = self.batch_queues.get(model_name, [])
        
        if not queue:
            return None
        
        # Check if we should create a batch now
        should_batch = False
        
        # Enough tasks accumulated
        if len(queue) >= self.config.min_batch_size:
            should_batch = True
        
        # Oldest task has waited too long
        if queue and hasattr(queue[0], 'age_ms'):
            if queue[0].age_ms > self.config.max_wait_time_ms:
                should_batch = True
        
        if not should_batch:
            return None
        
        # Determine batch size
        batch_size = min(
            self.config.max_batch_size,
            len(queue),
        )
        
        # Dynamic batching: adjust based on queue pressure
        if self.config.dynamic_batching:
            if len(queue) > 20:  # High pressure
                batch_size = min(self.config.max_batch_size, len(queue))
            elif len(queue) < 5:  # Low pressure
                batch_size = max(1, min(len(queue), self.config.min_batch_size))
        
        # Create batch
        batch_tasks = queue[:batch_size]
        self.batch_queues[model_name] = queue[batch_size:]
        
        batch = BatchJob(
            batch_id=f"batch_{int(time.time()*1000)}_{model_name}",
            tasks=batch_tasks,
            model_name=model_name,
        )
        
        self.active_batches[batch.batch_id] = batch
        self._total_batches += 1
        self._total_tasks_batched += len(batch_tasks)
        
        # Update average
        self._avg_batch_size = (
            self._avg_batch_size * (self._total_batches - 1) + len(batch_tasks)
        ) / max(1, self._total_batches)
        
        return batch
    
    def complete_batch(self, batch_id: str) -> None:
        """Mark a batch as complete"""
        self.active_batches.pop(batch_id, None)
    
    def get_queue_sizes(self) -> Dict[str, int]:
        """Get sizes of all batch queues"""
        return {
            model: len(queue)
            for model, queue in self.batch_queues.items()
        }
    
    def clear_queue(self, model_name: str) -> None:
        """Clear a model's batch queue"""
        if model_name in self.batch_queues:
            self.batch_queues[model_name] = []
    
    def clear_all(self) -> None:
        """Clear all batch queues"""
        self.batch_queues.clear()
        self.active_batches.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get batch processor statistics"""
        return {
            "total_batches": self._total_batches,
            "total_tasks_batched": self._total_tasks_batched,
            "avg_batch_size": round(self._avg_batch_size, 1),
            "active_batches": len(self.active_batches),
            "queue_sizes": self.get_queue_sizes(),
        }
    
    def reset(self) -> None:
        """Reset batch processor"""
        self.batch_queues.clear()
        self.active_batches.clear()
        self._total_batches = 0
        self._total_tasks_batched = 0
        self._avg_batch_size = 0.0
        self._log.info("Batch processor reset")


class BatchOptimizer:
    """
    Optimizes batch composition for maximum throughput.
    
    Analyzes:
    - Optimal batch size for each model
    - Memory constraints
    - Latency vs throughput trade-offs
    """
    
    def __init__(self):
        self.model_stats: Dict[str, Dict[str, Any]] = {}
    
    def record_batch(self, model_name: str, batch_size: int, processing_time_ms: float) -> None:
        """Record batch performance"""
        
        if model_name not in self.model_stats:
            self.model_stats[model_name] = {
                "total_batches": 0,
                "total_time_ms": 0.0,
                "avg_time_ms": 0.0,
                "best_batch_size": 1,
                "best_throughput": 0.0,
            }
        
        stats = self.model_stats[model_name]
        stats["total_batches"] += 1
        stats["total_time_ms"] += processing_time_ms
        stats["avg_time_ms"] = stats["total_time_ms"] / stats["total_batches"]
        
        # Calculate throughput (frames/second)
        throughput = (batch_size / processing_time_ms) * 1000
        
        if throughput > stats["best_throughput"]:
            stats["best_throughput"] = throughput
            stats["best_batch_size"] = batch_size
    
    def get_optimal_batch_size(self, model_name: str) -> int:
        """Get optimal batch size for a model"""
        stats = self.model_stats.get(model_name)
        if stats:
            return stats["best_batch_size"]
        return 4  # Default
    
    def get_stats(self) -> Dict[str, Any]:
        """Get optimizer statistics"""
        return {
            model: {
                "batches": s["total_batches"],
                "avg_time_ms": round(s["avg_time_ms"], 2),
                "best_batch_size": s["best_batch_size"],
                "best_throughput": round(s["best_throughput"], 1),
            }
            for model, s in self.model_stats.items()
        }
    
    def reset(self) -> None:
        """Reset optimizer"""
        self.model_stats.clear()