# backend/app/vision/inference/model_loader.py
"""
AEGIS AI - Model Loader
Manages model lifecycle: loading, unloading, caching, and versioning.
Supports ONNX, TensorRT, PyTorch, and PaddleOCR models.
"""

import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import structlog
import asyncio

logger = structlog.get_logger("aegis.vision.models")


class ModelFormat(str, Enum):
    """Supported model formats"""
    PYTORCH = "pytorch"
    TORCHSCRIPT = "torchscript"
    ONNX = "onnx"
    TENSORRT = "tensorrt"
    PADDLE = "paddle"
    OPENVINO = "openvino"
    COREML = "coreml"


class ModelStatus(str, Enum):
    """Model loading status"""
    NOT_LOADED = "not_loaded"
    LOADING = "loading"
    LOADED = "loaded"
    CACHED = "cached"
    UNLOADING = "unloading"
    ERROR = "error"


@dataclass
class ModelInfo:
    """Model information and metadata"""
    model_name: str
    model_type: str  # "yolo", "ocr", "classifier", etc.
    format: ModelFormat = ModelFormat.PYTORCH
    version: str = "1.0"
    
    # Paths
    model_path: str = ""
    config_path: str = ""
    
    # Memory
    estimated_memory_mb: float = 500
    actual_memory_mb: float = 0.0
    
    # Performance
    avg_inference_time_ms: float = 0.0
    max_batch_size: int = 8
    
    # Status
    status: ModelStatus = ModelStatus.NOT_LOADED
    loaded_at: Optional[float] = None
    last_used: float = 0.0
    usage_count: int = 0
    
    # GPU
    gpu_id: int = 0
    use_fp16: bool = True
    use_int8: bool = False
    
    # Metadata
    input_size: Tuple[int, int] = (640, 640)
    num_classes: int = 80
    labels: List[str] = field(default_factory=list)


class ModelRegistry:
    """Registry of all available models"""
    
    def __init__(self):
        self.models: Dict[str, ModelInfo] = {}
        self._log = logger.bind(component="model_registry")
    
    def register(self, model_info: ModelInfo) -> None:
        """Register a model"""
        self.models[model_info.model_name] = model_info
        self._log.info(f"Model registered: {model_info.model_name} v{model_info.version}")
    
    def unregister(self, model_name: str) -> bool:
        """Remove a model from registry"""
        if model_name in self.models:
            del self.models[model_name]
            return True
        return False
    
    def get(self, model_name: str) -> Optional[ModelInfo]:
        """Get model info"""
        return self.models.get(model_name)
    
    def list_models(self) -> List[str]:
        """List all registered models"""
        return list(self.models.keys())
    
    def get_by_type(self, model_type: str) -> List[ModelInfo]:
        """Get models by type"""
        return [m for m in self.models.values() if m.model_type == model_type]


class ModelLoader:
    """
    Manages model loading and unloading lifecycle.
    
    Features:
    - Lazy loading (load on first use)
    - Model caching with LRU eviction
    - Format auto-detection
    - FP16/INT8 optimization
    - Warm-up inference
    - Version management
    - Health checking
    """
    
    def __init__(self, registry: ModelRegistry, gpu_manager: Any = None):
        self.registry = registry
        self.gpu_manager = gpu_manager
        
        # Loaded model instances
        self.loaded_models: Dict[str, Any] = {}
        
        # Loading locks (prevent double-load)
        self._loading_locks: Dict[str, asyncio.Lock] = {}
        
        # Statistics
        self._total_loads = 0
        self._total_unloads = 0
        self._load_times: Dict[str, List[float]] = {}
        
        self._log = logger.bind(component="model_loader")
    
    async def load_model(
        self,
        model_name: str,
        gpu_id: int = 0,
        force_reload: bool = False,
    ) -> Optional[Any]:
        """
        Load a model into memory.
        
        Args:
            model_name: Name of model to load
            gpu_id: GPU to load on
            force_reload: Force reload even if already loaded
            
        Returns:
            Loaded model instance or None
        """
        
        # Check if already loaded
        if model_name in self.loaded_models and not force_reload:
            model_info = self.registry.get(model_name)
            if model_info:
                model_info.last_used = time.time()
                model_info.usage_count += 1
            return self.loaded_models[model_name]
        
        # Get model info
        model_info = self.registry.get(model_name)
        if not model_info:
            self._log.error(f"Model not registered: {model_name}")
            return None
        
        # Prevent concurrent loading
        if model_name not in self._loading_locks:
            self._loading_locks[model_name] = asyncio.Lock()
        
        async with self._loading_locks[model_name]:
            # Double-check after acquiring lock
            if model_name in self.loaded_models and not force_reload:
                return self.loaded_models[model_name]
            
            # Check GPU memory
            if self.gpu_manager:
                memory_mb = model_info.estimated_memory_mb
                alloc_id = self.gpu_manager.allocate_model(model_name, gpu_id)
                
                if alloc_id is None:
                    self._log.error(f"Insufficient GPU memory for {model_name}")
                    return None
            
            # Load model
            self._log.info(f"Loading model: {model_name}")
            model_info.status = ModelStatus.LOADING
            
            start_time = time.time()
            
            try:
                model = self._load_by_format(model_info, gpu_id)
                
                if model is not None:
                    self.loaded_models[model_name] = model
                    model_info.status = ModelStatus.LOADED
                    model_info.loaded_at = time.time()
                    model_info.last_used = time.time()
                    
                    load_time = (time.time() - start_time) * 1000
                    
                    # Track load times
                    if model_name not in self._load_times:
                        self._load_times[model_name] = []
                    self._load_times[model_name].append(load_time)
                    
                    self._total_loads += 1
                    
                    self._log.info(
                        f"Model loaded: {model_name} in {load_time:.0f}ms"
                    )
                    
                    # Warm up with dummy inference
                    await self._warmup_model(model_name, model, model_info)
                    
                    return model
                else:
                    model_info.status = ModelStatus.ERROR
                    
            except Exception as e:
                model_info.status = ModelStatus.ERROR
                self._log.error(f"Failed to load {model_name}: {e}")
        
        return None
    
    def _load_by_format(self, info: ModelInfo, gpu_id: int) -> Any:
        """Load model based on its format"""
        
        model_path = info.model_path
        
        if info.format == ModelFormat.PYTORCH:
            return self._load_pytorch(model_path, gpu_id, info)
        elif info.format == ModelFormat.TORCHSCRIPT:
            return self._load_torchscript(model_path, gpu_id)
        elif info.format == ModelFormat.ONNX:
            return self._load_onnx(model_path, gpu_id)
        elif info.format == ModelFormat.TENSORRT:
            return self._load_tensorrt(model_path)
        elif info.format == ModelFormat.PADDLE:
            return self._load_paddle(info)
        else:
            raise ValueError(f"Unsupported format: {info.format}")
    
    def _load_pytorch(self, path: str, gpu_id: int, info: ModelInfo) -> Any:
        """Load PyTorch model"""
        import torch
        
        # Load checkpoint
        checkpoint = torch.load(path, map_location=f'cuda:{gpu_id}')
        
        # Extract model based on type
        if info.model_type == "yolo":
            from ultralytics import YOLO
            model = YOLO(path)
        else:
            model = checkpoint
        
        # Convert to FP16 if requested
        if info.use_fp16 and torch.cuda.is_available():
            model = model.half()
        
        model.eval()
        return model
    
    def _load_torchscript(self, path: str, gpu_id: int) -> Any:
        """Load TorchScript model"""
        import torch
        return torch.jit.load(path, map_location=f'cuda:{gpu_id}')
    
    def _load_onnx(self, path: str, gpu_id: int) -> Any:
        """Load ONNX model"""
        import onnxruntime as ort
        
        providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        return ort.InferenceSession(path, providers=providers)
    
    def _load_tensorrt(self, path: str) -> Any:
        """Load TensorRT engine"""
        import tensorrt as trt
        
        with open(path, 'rb') as f:
            runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
            return runtime.deserialize_cuda_engine(f.read())
    
    def _load_paddle(self, info: ModelInfo) -> Any:
        """Load PaddleOCR model"""
        from paddleocr import PaddleOCR
        
        return PaddleOCR(
            use_angle_cls=True,
            lang='en',
            use_gpu=True,
            show_log=False,
        )
    
    async def _warmup_model(self, model_name: str, model: Any, info: ModelInfo) -> None:
        """Warm up model with dummy inference"""
        
        try:
            import numpy as np
            
            # Create dummy input
            h, w = info.input_size
            dummy_input = np.random.randint(0, 255, (h, w, 3), dtype=np.uint8)
            
            # Run inference
            if info.model_type == "yolo":
                model(dummy_input, verbose=False)
            elif info.model_type == "ocr":
                model.ocr(dummy_input)
            
            self._log.debug(f"Model warmed up: {model_name}")
            
        except Exception as e:
            self._log.warning(f"Model warm-up failed: {model_name} - {e}")
    
    async def unload_model(self, model_name: str) -> bool:
        """Unload a model from memory"""
        
        if model_name not in self.loaded_models:
            return False
        
        model_info = self.registry.get(model_name)
        if model_info:
            model_info.status = ModelStatus.UNLOADING
        
        # Remove from loaded models
        del self.loaded_models[model_name]
        
        # Deallocate GPU memory
        if self.gpu_manager:
            for gpu_id in self.gpu_manager.devices:
                for alloc_id, alloc in list(self.gpu_manager.devices[gpu_id].allocations.items()):
                    if alloc.model_name == model_name:
                        self.gpu_manager.deallocate_model(alloc_id, gpu_id)
        
        # Clear CUDA cache
        try:
            import torch
            torch.cuda.empty_cache()
        except ImportError:
            pass
        
        if model_info:
            model_info.status = ModelStatus.NOT_LOADED
        
        self._total_unloads += 1
        self._log.info(f"Model unloaded: {model_name}")
        
        return True
    
    def is_loaded(self, model_name: str) -> bool:
        """Check if model is loaded"""
        return model_name in self.loaded_models
    
    def get_loaded_models(self) -> List[str]:
        """Get list of loaded models"""
        return list(self.loaded_models.keys())
    
    def get_model(self, model_name: str) -> Optional[Any]:
        """Get loaded model instance"""
        return self.loaded_models.get(model_name)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get model loader statistics"""
        return {
            "loaded_models": len(self.loaded_models),
            "total_loads": self._total_loads,
            "total_unloads": self._total_unloads,
            "load_times": {
                name: {
                    "avg_ms": round(sum(times) / len(times), 1),
                    "count": len(times),
                }
                for name, times in self._load_times.items()
            },
            "models": self.get_loaded_models(),
        }
    
    def reset(self) -> None:
        """Reset model loader"""
        self.loaded_models.clear()
        self._total_loads = 0
        self._total_unloads = 0
        self._load_times.clear()
        self._log.info("Model loader reset")