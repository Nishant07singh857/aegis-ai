# backend/app/vision/inference/gpu_manager.py
"""
AEGIS AI - GPU Manager
Dynamic GPU memory allocation and management.
Optimizes GPU utilization across multiple models and feeds.
"""

import time
import threading
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.vision.gpu")


class GPUState(str, Enum):
    """GPU device states"""
    AVAILABLE = "available"
    IN_USE = "in_use"
    LOW_MEMORY = "low_memory"
    FULL = "full"
    ERROR = "error"
    OFFLINE = "offline"


@dataclass
class MemoryAllocation:
    """GPU memory allocation record"""
    allocation_id: str
    model_name: str
    memory_mb: float
    allocated_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    priority: int = 50
    is_pinned: bool = False  # Don't unload
    usage_count: int = 0


@dataclass
class GPUDevice:
    """GPU device information"""
    device_id: int
    name: str = ""
    total_memory_mb: float = 0.0
    used_memory_mb: float = 0.0
    free_memory_mb: float = 0.0
    utilization_pct: float = 0.0
    temperature_c: float = 0.0
    state: GPUState = GPUState.AVAILABLE
    
    # Active allocations
    allocations: Dict[str, MemoryAllocation] = field(default_factory=dict)
    
    # Statistics
    peak_memory_mb: float = 0.0
    total_allocations: int = 0
    total_deallocations: int = 0
    
    @property
    def memory_usage_pct(self) -> float:
        """Memory usage percentage"""
        if self.total_memory_mb > 0:
            return (self.used_memory_mb / self.total_memory_mb) * 100
        return 0.0
    
    @property
    def can_allocate(self, size_mb: float) -> bool:
        """Check if can allocate more memory"""
        return self.free_memory_mb >= size_mb * 1.1  # 10% buffer
    
    def allocate(self, allocation: MemoryAllocation) -> bool:
        """Allocate memory on this GPU"""
        if not self.can_allocate(allocation.memory_mb):
            return False
        
        self.allocations[allocation.allocation_id] = allocation
        self.used_memory_mb += allocation.memory_mb
        self.free_memory_mb = self.total_memory_mb - self.used_memory_mb
        self.peak_memory_mb = max(self.peak_memory_mb, self.used_memory_mb)
        self.total_allocations += 1
        
        return True
    
    def deallocate(self, allocation_id: str) -> Optional[float]:
        """Deallocate memory"""
        allocation = self.allocations.pop(allocation_id, None)
        if allocation:
            self.used_memory_mb -= allocation.memory_mb
            self.free_memory_mb = self.total_memory_mb - self.used_memory_mb
            self.total_deallocations += 1
            return allocation.memory_mb
        return None


class GPUManager:
    """
    Dynamic GPU memory manager.
    
    Features:
    - Multi-GPU support
    - Memory allocation tracking
    - Automatic model swapping (LRU)
    - Memory defragmentation
    - Priority-based allocation
    - GPU health monitoring
    - Temperature monitoring
    - Peak memory tracking
    """
    
    def __init__(self, gpu_ids: List[int] = None):
        self.gpu_ids = gpu_ids or [0]
        self.devices: Dict[int, GPUDevice] = {}
        
        # Model memory requirements (estimated)
        self.model_memory = {
            "yolov8n": 200,      # Nano - ~200MB
            "yolov8s": 400,      # Small - ~400MB
            "yolov8m": 800,      # Medium - ~800MB
            "yolov8l": 1500,     # Large - ~1.5GB
            "paddleocr": 500,    # PaddleOCR - ~500MB
            "bytetrack": 100,    # ByteTrack - ~100MB
            "resnet50": 300,     # ResNet50 - ~300MB
            "efficientnet": 200, # EfficientNet - ~200MB
        }
        
        # Model priority (higher = keep longer)
        self.model_priorities = {
            "yolov8n": 80,
            "yolov8m": 70,
            "paddleocr": 90,    # Always needed
            "bytetrack": 85,    # Always needed
            "resnet50": 50,
            "efficientnet": 40,
        }
        
        # LRU tracking
        self._lru_order: List[str] = []
        
        # Monitoring
        self._monitor_thread: Optional[threading.Thread] = None
        self._running = False
        
        # Initialize devices
        self._initialize_devices()
        
        self._log = logger.bind(component="gpu_manager")
    
    def _initialize_devices(self) -> None:
        """Initialize GPU device tracking"""
        
        try:
            import torch
            
            for gpu_id in self.gpu_ids:
                if torch.cuda.is_available() and gpu_id < torch.cuda.device_count():
                    props = torch.cuda.get_device_properties(gpu_id)
                    
                    device = GPUDevice(
                        device_id=gpu_id,
                        name=props.name,
                        total_memory_mb=props.total_memory / (1024 * 1024),
                        free_memory_mb=props.total_memory / (1024 * 1024),
                    )
                    
                    self.devices[gpu_id] = device
                    
                    self._log.info(
                        f"GPU {gpu_id}: {props.name} "
                        f"({device.total_memory_mb:.0f}MB)"
                    )
                else:
                    # CPU-only mode
                    device = GPUDevice(
                        device_id=gpu_id,
                        name="CPU (no GPU)",
                        total_memory_mb=16000,  # Assume 16GB RAM
                        free_memory_mb=8000,
                    )
                    self.devices[gpu_id] = device
                    
        except ImportError:
            self._log.warning("PyTorch not available - using CPU mode")
            
            for gpu_id in self.gpu_ids:
                device = GPUDevice(
                    device_id=gpu_id,
                    name="CPU",
                    total_memory_mb=16000,
                    free_memory_mb=8000,
                )
                self.devices[gpu_id] = device
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MEMORY MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def allocate_model(
        self,
        model_name: str,
        gpu_id: int = 0,
        pin: bool = False,
    ) -> Optional[str]:
        """
        Allocate GPU memory for a model.
        
        Args:
            model_name: Name of the model
            gpu_id: Target GPU
            pin: Whether to pin in memory (don't auto-unload)
            
        Returns:
            allocation_id if successful, None if failed
        """
        
        device = self.devices.get(gpu_id)
        if not device:
            return None
        
        # Get estimated memory
        memory_mb = self.model_memory.get(model_name, 500)
        priority = self.model_priorities.get(model_name, 50)
        
        # Check if already allocated
        for alloc_id, alloc in device.allocations.items():
            if alloc.model_name == model_name:
                alloc.last_used = time.time()
                alloc.usage_count += 1
                return alloc_id
        
        # Try to allocate
        allocation = MemoryAllocation(
            allocation_id=f"{model_name}_{int(time.time()*1000)}",
            model_name=model_name,
            memory_mb=memory_mb,
            priority=priority,
            is_pinned=pin,
        )
        
        if device.can_allocate(memory_mb):
            device.allocate(allocation)
            self._lru_order.append(allocation.allocation_id)
            return allocation.allocation_id
        
        # Need to free memory
        freed = self._free_memory(device, memory_mb)
        
        if freed >= memory_mb:
            device.allocate(allocation)
            self._lru_order.append(allocation.allocation_id)
            return allocation.allocation_id
        
        self._log.warning(f"Cannot allocate {memory_mb}MB for {model_name} on GPU {gpu_id}")
        return None
    
    def deallocate_model(self, allocation_id: str, gpu_id: int = 0) -> bool:
        """Deallocate a model from GPU memory"""
        
        device = self.devices.get(gpu_id)
        if not device:
            return False
        
        freed = device.deallocate(allocation_id)
        if freed:
            if allocation_id in self._lru_order:
                self._lru_order.remove(allocation_id)
            return True
        
        return False
    
    def _free_memory(self, device: GPUDevice, required_mb: float) -> float:
        """
        Free GPU memory by unloading least recently used models.
        
        Args:
            device: GPU device
            required_mb: Amount of memory needed
            
        Returns:
            Amount of memory freed
        """
        
        freed = 0.0
        
        # Sort allocations by last used (oldest first), but respect priority
        candidates = sorted(
            device.allocations.items(),
            key=lambda x: (
                -x[1].priority,  # Higher priority = keep
                x[1].last_used,  # Older = unload first
            )
        )
        
        for alloc_id, alloc in candidates:
            if alloc.is_pinned:
                continue  # Don't unload pinned models
            
            if freed >= required_mb:
                break
            
            if self.deallocate_model(alloc_id, device.device_id):
                freed += alloc.memory_mb
                self._log.debug(
                    f"Unloaded {alloc.model_name} from GPU {device.device_id} "
                    f"(freed {alloc.memory_mb:.0f}MB)"
                )
        
        return freed
    
    def pin_model(self, allocation_id: str, gpu_id: int = 0) -> bool:
        """Pin a model in memory (prevent auto-unload)"""
        
        device = self.devices.get(gpu_id)
        if not device:
            return False
        
        alloc = device.allocations.get(allocation_id)
        if alloc:
            alloc.is_pinned = True
            return True
        
        return False
    
    def unpin_model(self, allocation_id: str, gpu_id: int = 0) -> bool:
        """Unpin a model from memory"""
        
        device = self.devices.get(gpu_id)
        if not device:
            return False
        
        alloc = device.allocations.get(allocation_id)
        if alloc:
            alloc.is_pinned = False
            return True
        
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # MONITORING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def start_monitoring(self, interval: float = 5.0) -> None:
        """Start GPU monitoring thread"""
        
        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
        )
        self._monitor_thread.start()
    
    def stop_monitoring(self) -> None:
        """Stop GPU monitoring"""
        self._running = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2)
    
    def _monitor_loop(self, interval: float) -> None:
        """GPU monitoring loop"""
        
        while self._running:
            try:
                self._update_device_stats()
                time.sleep(interval)
            except Exception as e:
                logger.error(f"GPU monitor error: {e}")
    
    def _update_device_stats(self) -> None:
        """Update GPU device statistics"""
        
        try:
            import torch
            
            for gpu_id, device in self.devices.items():
                if torch.cuda.is_available() and gpu_id < torch.cuda.device_count():
                    # Get real GPU stats
                    mem_info = torch.cuda.mem_get_info(gpu_id)
                    device.free_memory_mb = mem_info[0] / (1024 * 1024)
                    device.total_memory_mb = mem_info[1] / (1024 * 1024)
                    device.used_memory_mb = device.total_memory_mb - device.free_memory_mb
                    device.utilization_pct = torch.cuda.utilization(gpu_id) if hasattr(torch.cuda, 'utilization') else 0
                    
                    # Update state
                    if device.memory_usage_pct > 90:
                        device.state = GPUState.FULL
                    elif device.memory_usage_pct > 75:
                        device.state = GPUState.LOW_MEMORY
                    else:
                        device.state = GPUState.AVAILABLE
                        
        except ImportError:
            pass
        except Exception as e:
            logger.error(f"GPU stats update error: {e}")
    
    def get_best_gpu(self, required_mb: float = 500) -> int:
        """Get the best GPU for allocation"""
        
        best_gpu = 0
        best_free = -1
        
        for gpu_id, device in self.devices.items():
            if device.state in [GPUState.AVAILABLE, GPUState.IN_USE]:
                if device.free_memory_mb > best_free and device.can_allocate(required_mb):
                    best_free = device.free_memory_mb
                    best_gpu = gpu_id
        
        return best_gpu
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_device_info(self, gpu_id: int = 0) -> Optional[Dict[str, Any]]:
        """Get GPU device information"""
        device = self.devices.get(gpu_id)
        if not device:
            return None
        
        return {
            "device_id": device.device_id,
            "name": device.name,
            "memory": {
                "total_mb": round(device.total_memory_mb, 0),
                "used_mb": round(device.used_memory_mb, 0),
                "free_mb": round(device.free_memory_mb, 0),
                "usage_pct": round(device.memory_usage_pct, 1),
            },
            "state": device.state.value,
            "temperature_c": round(device.temperature_c, 1),
            "allocations": {
                alloc_id: {
                    "model": alloc.model_name,
                    "memory_mb": alloc.memory_mb,
                    "pinned": alloc.is_pinned,
                    "usage_count": alloc.usage_count,
                }
                for alloc_id, alloc in device.allocations.items()
            },
            "peak_memory_mb": round(device.peak_memory_mb, 0),
        }
    
    def get_all_devices(self) -> Dict[int, Dict[str, Any]]:
        """Get all GPU devices"""
        return {
            gpu_id: self.get_device_info(gpu_id)
            for gpu_id in self.devices
        }
    
    def get_memory_summary(self) -> Dict[str, Any]:
        """Get memory usage summary"""
        total_used = sum(d.used_memory_mb for d in self.devices.values())
        total_free = sum(d.free_memory_mb for d in self.devices.values())
        total_mem = sum(d.total_memory_mb for d in self.devices.values())
        
        return {
            "total_memory_mb": round(total_mem, 0),
            "used_memory_mb": round(total_used, 0),
            "free_memory_mb": round(total_free, 0),
            "usage_pct": round((total_used / total_mem * 100) if total_mem > 0 else 0, 1),
            "devices": len(self.devices),
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get GPU manager statistics"""
        return {
            "devices": self.get_all_devices(),
            "memory": self.get_memory_summary(),
            "model_memory_estimates": self.model_memory,
        }
    
    def reset(self) -> None:
        """Reset GPU manager"""
        for device in self.devices.values():
            device.allocations.clear()
            device.used_memory_mb = 0
            device.free_memory_mb = device.total_memory_mb
        self._lru_order.clear()
        self._log.info("GPU manager reset")