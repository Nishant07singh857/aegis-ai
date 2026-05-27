# backend/app/vision/models/model_registry.py
"""
AEGIS AI - Model Registry
Centralized model registry with versioning, metadata,
and lifecycle management for all vision models.
"""

import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.vision.registry")


class ModelStatus(str, Enum):
    """Model status"""
    REGISTERED = "registered"
    DOWNLOADING = "downloading"
    LOADING = "loading"
    LOADED = "loaded"
    WARMING_UP = "warming_up"
    READY = "ready"
    UNLOADING = "unloading"
    UNLOADED = "unloaded"
    ERROR = "error"
    DEPRECATED = "deprecated"


class ModelFramework(str, Enum):
    """Model frameworks"""
    PYTORCH = "pytorch"
    TENSORRT = "tensorrt"
    ONNX = "onnx"
    PADDLE = "paddle"
    OPENVINO = "openvino"
    TFLITE = "tflite"


@dataclass
class ModelVersion:
    """Model version information"""
    version: str
    path: str
    framework: ModelFramework = ModelFramework.PYTORCH
    created_at: float = field(default_factory=time.time)
    
    # Performance
    avg_inference_ms: float = 0.0
    accuracy: float = 0.0
    memory_mb: float = 0.0
    
    # Compatibility
    min_driver_version: str = ""
    requires_gpu: bool = True
    
    # Metadata
    checksum: str = ""
    trained_on: str = ""
    notes: str = ""


@dataclass
class ModelEntry:
    """Complete model registry entry"""
    model_name: str
    model_type: str  # "yolo", "ocr", "tracking", "classifier"
    description: str = ""
    
    # Versions
    versions: List[ModelVersion] = field(default_factory=list)
    active_version: Optional[str] = None
    
    # Status
    status: ModelStatus = ModelStatus.REGISTERED
    is_loaded: bool = False
    
    # Configuration
    default_config: Dict[str, Any] = field(default_factory=dict)
    
    # Performance targets
    target_fps: int = 30
    max_latency_ms: float = 100.0
    
    # Dependencies
    depends_on: List[str] = field(default_factory=list)
    
    # Usage
    total_inferences: int = 0
    last_used: float = 0.0
    created_at: float = field(default_factory=time.time)
    
    # Tags
    tags: List[str] = field(default_factory=list)
    
    def get_active_version(self) -> Optional[ModelVersion]:
        """Get the active model version"""
        if self.active_version:
            for v in self.versions:
                if v.version == self.active_version:
                    return v
        elif self.versions:
            return self.versions[-1]  # Latest version
        return None
    
    def add_version(self, version: ModelVersion) -> None:
        """Add a new version"""
        # Check for duplicate
        for v in self.versions:
            if v.version == version.version:
                logger.warning(f"Version {version.version} already exists for {self.model_name}")
                return
        
        self.versions.append(version)
        self.versions.sort(key=lambda v: v.created_at)
    
    def set_active_version(self, version: str) -> bool:
        """Set the active version"""
        for v in self.versions:
            if v.version == version:
                self.active_version = version
                return True
        return False


class ModelRegistry:
    """
    Centralized model registry for all vision models.
    
    Features:
    - Model registration and discovery
    - Version management
    - Active version tracking
    - Dependency management
    - Performance monitoring
    - Model metadata storage
    """
    
    def __init__(self):
        self.models: Dict[str, ModelEntry] = {}
        self._log = logger.bind(component="model_registry")
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # REGISTRATION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def register(
        self,
        model_name: str,
        model_type: str,
        description: str = "",
        default_config: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> ModelEntry:
        """
        Register a new model.
        
        Args:
            model_name: Unique model name
            model_type: Type of model
            description: Model description
            default_config: Default configuration
            tags: Optional tags
            
        Returns:
            ModelEntry
        """
        
        if model_name in self.models:
            self._log.warning(f"Model already registered: {model_name}")
            return self.models[model_name]
        
        entry = ModelEntry(
            model_name=model_name,
            model_type=model_type,
            description=description,
            default_config=default_config or {},
            tags=tags or [],
        )
        
        self.models[model_name] = entry
        
        self._log.info(f"Model registered: {model_name} (type: {model_type})")
        
        return entry
    
    def unregister(self, model_name: str) -> bool:
        """Remove a model from registry"""
        if model_name in self.models:
            del self.models[model_name]
            self._log.info(f"Model unregistered: {model_name}")
            return True
        return False
    
    def add_version(
        self,
        model_name: str,
        version: str,
        path: str,
        framework: ModelFramework = ModelFramework.PYTORCH,
        **kwargs,
    ) -> Optional[ModelVersion]:
        """Add a new version to a registered model"""
        
        entry = self.models.get(model_name)
        if not entry:
            self._log.error(f"Model not found: {model_name}")
            return None
        
        model_version = ModelVersion(
            version=version,
            path=path,
            framework=framework,
            **kwargs,
        )
        
        entry.add_version(model_version)
        
        # Auto-set as active if first version
        if len(entry.versions) == 1:
            entry.active_version = version
        
        self._log.info(f"Version added: {model_name} v{version}")
        
        return model_version
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # QUERIES
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get(self, model_name: str) -> Optional[ModelEntry]:
        """Get model entry by name"""
        return self.models.get(model_name)
    
    def get_version(self, model_name: str, version: str) -> Optional[ModelVersion]:
        """Get specific model version"""
        entry = self.models.get(model_name)
        if entry:
            for v in entry.versions:
                if v.version == version:
                    return v
        return None
    
    def get_active_version(self, model_name: str) -> Optional[ModelVersion]:
        """Get active version of a model"""
        entry = self.models.get(model_name)
        if entry:
            return entry.get_active_version()
        return None
    
    def list_models(self, model_type: Optional[str] = None) -> List[str]:
        """List all registered models, optionally filtered by type"""
        if model_type:
            return [
                name for name, entry in self.models.items()
                if entry.model_type == model_type
            ]
        return list(self.models.keys())
    
    def get_by_type(self, model_type: str) -> List[ModelEntry]:
        """Get all models of a specific type"""
        return [
            entry for entry in self.models.values()
            if entry.model_type == model_type
        ]
    
    def get_by_tag(self, tag: str) -> List[ModelEntry]:
        """Get models by tag"""
        return [
            entry for entry in self.models.values()
            if tag in entry.tags
        ]
    
    def get_loaded_models(self) -> List[str]:
        """Get list of currently loaded models"""
        return [
            name for name, entry in self.models.items()
            if entry.is_loaded
        ]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATUS MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def set_status(self, model_name: str, status: ModelStatus) -> bool:
        """Update model status"""
        entry = self.models.get(model_name)
        if entry:
            entry.status = status
            
            if status == ModelStatus.LOADED:
                entry.is_loaded = True
            elif status == ModelStatus.UNLOADED:
                entry.is_loaded = False
            
            return True
        return False
    
    def record_inference(self, model_name: str, latency_ms: float) -> None:
        """Record an inference for performance tracking"""
        entry = self.models.get(model_name)
        if entry:
            entry.total_inferences += 1
            entry.last_used = time.time()
            
            # Update active version stats
            version = entry.get_active_version()
            if version:
                n = max(1, entry.total_inferences)
                version.avg_inference_ms = (
                    version.avg_inference_ms * (n - 1) + latency_ms
                ) / n
    
    def set_active_version(self, model_name: str, version: str) -> bool:
        """Set active version for a model"""
        entry = self.models.get(model_name)
        if entry:
            return entry.set_active_version(version)
        return False
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DEPENDENCY CHECKING
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def check_dependencies(self, model_name: str) -> List[str]:
        """Check if all dependencies are satisfied"""
        entry = self.models.get(model_name)
        if not entry:
            return [f"Model not found: {model_name}"]
        
        missing = []
        for dep in entry.depends_on:
            if dep not in self.models:
                missing.append(dep)
            elif not self.models[dep].is_loaded:
                missing.append(f"{dep} (not loaded)")
        
        return missing
    
    def get_load_order(self, model_names: List[str]) -> List[str]:
        """Get correct load order respecting dependencies"""
        
        loaded = set()
        order = []
        
        def load_with_deps(name: str):
            if name in loaded:
                return
            
            entry = self.models.get(name)
            if entry:
                for dep in entry.depends_on:
                    load_with_deps(dep)
                
                loaded.add(name)
                order.append(name)
        
        for name in model_names:
            load_with_deps(name)
        
        return order
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATISTICS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    def get_model_info(self, model_name: str) -> Optional[Dict[str, Any]]:
        """Get detailed model information"""
        entry = self.models.get(model_name)
        if not entry:
            return None
        
        active_ver = entry.get_active_version()
        
        return {
            "name": entry.model_name,
            "type": entry.model_type,
            "description": entry.description,
            "status": entry.status.value,
            "is_loaded": entry.is_loaded,
            "total_inferences": entry.total_inferences,
            "active_version": entry.active_version,
            "versions": [
                {
                    "version": v.version,
                    "framework": v.framework.value,
                    "avg_inference_ms": round(v.avg_inference_ms, 2),
                    "accuracy": round(v.accuracy, 4),
                    "memory_mb": round(v.memory_mb, 0),
                }
                for v in entry.versions
            ],
            "active_version_details": {
                "version": active_ver.version,
                "path": active_ver.path,
                "framework": active_ver.framework.value,
                "avg_inference_ms": round(active_ver.avg_inference_ms, 2),
            } if active_ver else None,
            "dependencies": entry.depends_on,
            "tags": entry.tags,
        }
    
    def get_all_models_info(self) -> Dict[str, Any]:
        """Get info for all registered models"""
        return {
            name: self.get_model_info(name)
            for name in self.models
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics"""
        return {
            "total_models": len(self.models),
            "loaded_models": len(self.get_loaded_models()),
            "by_type": {
                mtype: len(self.get_by_type(mtype))
                for mtype in set(m.model_type for m in self.models.values())
            },
            "models": self.get_all_models_info(),
        }
    
    def reset(self) -> None:
        """Reset registry"""
        self.models.clear()
        self._log.info("Model registry reset")