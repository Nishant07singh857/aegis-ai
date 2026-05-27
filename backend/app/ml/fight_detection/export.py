# backend/app/ml/fight_detection/export.py
"""
AEGIS AI - Model Export
Export trained models to production formats:
ONNX, TensorRT, TorchScript, and CoreML.
"""

import os
import time
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass
import structlog
import torch

from .model import FightDetectionModel

logger = structlog.get_logger("aegis.ml.export")


@dataclass
class ExportConfig:
    """Export configuration"""
    # Input shape
    input_size: Tuple[int, int] = (640, 640)
    batch_size: int = 1
    channels: int = 3
    
    # Formats
    export_onnx: bool = True
    export_torchscript: bool = True
    export_tensorrt: bool = False
    export_coreml: bool = False
    
    # ONNX
    onnx_opset: int = 17
    onnx_dynamic_batch: bool = True
    onnx_simplify: bool = True
    
    # TensorRT
    tensorrt_fp16: bool = True
    tensorrt_int8: bool = False
    tensorrt_workspace_mb: int = 4096
    
    # TorchScript
    torchscript_method: str = "trace"  # trace, script
    
    # Optimization
    fuse_layers: bool = True
    remove_identity: bool = True
    
    # Output
    output_dir: str = "exported_models"
    model_name: str = "fight_detection"


@dataclass
class ExportResult:
    """Export result"""
    format: str
    path: str
    file_size_mb: float = 0.0
    export_time: float = 0.0
    success: bool = False
    error: Optional[str] = None


class ModelExporter:
    """
    Export models to production formats.
    
    Supported formats:
    - ONNX (cross-platform inference)
    - TensorRT (NVIDIA GPU optimization)
    - TorchScript (PyTorch deployment)
    - CoreML (Apple devices)
    """
    
    def __init__(self, config: Optional[ExportConfig] = None):
        self.config = config or ExportConfig()
        self.results: List[ExportResult] = []
    
    def export(
        self,
        model: FightDetectionModel,
        checkpoint_path: Optional[str] = None,
    ) -> List[ExportResult]:
        """
        Export model to all configured formats.
        
        Args:
            model: Trained model
            checkpoint_path: Path to checkpoint (loads state dict if provided)
            
        Returns:
            List of ExportResult for each format
        """
        
        self.results = []
        
        # Load checkpoint if provided
        if checkpoint_path and os.path.exists(checkpoint_path):
            checkpoint = torch.load(checkpoint_path, map_location='cpu')
            model.load_state_dict(checkpoint['model_state_dict'])
            logger.info(f"Loaded checkpoint from {checkpoint_path}")
        
        model.eval()
        
        # Create output directory
        os.makedirs(self.config.output_dir, exist_ok=True)
        
        # Export to each format
        if self.config.export_torchscript:
            result = self._export_torchscript(model)
            self.results.append(result)
        
        if self.config.export_onnx:
            result = self._export_onnx(model)
            self.results.append(result)
        
        if self.config.export_tensorrt:
            result = self._export_tensorrt(model)
            self.results.append(result)
        
        if self.config.export_coreml:
            result = self._export_coreml(model)
            self.results.append(result)
        
        # Summary
        successful = sum(1 for r in self.results if r.success)
        logger.info(f"Export complete: {successful}/{len(self.results)} formats successful")
        
        return self.results
    
    def _export_torchscript(self, model: FightDetectionModel) -> ExportResult:
        """Export to TorchScript format"""
        
        logger.info("Exporting to TorchScript...")
        start_time = time.time()
        
        try:
            output_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.torchscript.pt",
            )
            
            # Create dummy input
            dummy_input = torch.randn(
                self.config.batch_size,
                self.config.channels,
                *self.config.input_size,
            )
            
            if self.config.torchscript_method == "trace":
                traced_model = torch.jit.trace(model, dummy_input)
            else:
                traced_model = torch.jit.script(model)
            
            # Optimize
            if self.config.fuse_layers:
                traced_model = torch.jit.optimize_for_inference(traced_model)
            
            # Save
            torch.jit.save(traced_model, output_path)
            
            # Get file size
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            
            return ExportResult(
                format="torchscript",
                path=output_path,
                file_size_mb=round(file_size, 2),
                export_time=time.time() - start_time,
                success=True,
            )
            
        except Exception as e:
            logger.error(f"TorchScript export failed: {e}")
            return ExportResult(
                format="torchscript",
                path="",
                export_time=time.time() - start_time,
                success=False,
                error=str(e),
            )
    
    def _export_onnx(self, model: FightDetectionModel) -> ExportResult:
        """Export to ONNX format"""
        
        logger.info("Exporting to ONNX...")
        start_time = time.time()
        
        try:
            output_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.onnx",
            )
            
            # Create dummy input
            dummy_input = torch.randn(
                self.config.batch_size,
                self.config.channels,
                *self.config.input_size,
            )
            
            # Dynamic batch size
            dynamic_axes = None
            if self.config.onnx_dynamic_batch:
                dynamic_axes = {
                    'input': {0: 'batch_size'},
                    'output': {0: 'batch_size'},
                }
            
            # Export
            torch.onnx.export(
                model,
                dummy_input,
                output_path,
                export_params=True,
                opset_version=self.config.onnx_opset,
                do_constant_folding=True,
                input_names=['input'],
                output_names=['output_p3', 'output_p4', 'output_p5'],
                dynamic_axes=dynamic_axes,
            )
            
            # Simplify ONNX model
            if self.config.onnx_simplify:
                try:
                    import onnxsim
                    import onnx
                    
                    onnx_model = onnx.load(output_path)
                    simplified, check = onnxsim.simplify(onnx_model)
                    
                    if check:
                        onnx.save(simplified, output_path)
                        logger.info("ONNX model simplified")
                except ImportError:
                    logger.warning("onnxsim not installed - skipping simplification")
            
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            
            return ExportResult(
                format="onnx",
                path=output_path,
                file_size_mb=round(file_size, 2),
                export_time=time.time() - start_time,
                success=True,
            )
            
        except Exception as e:
            logger.error(f"ONNX export failed: {e}")
            return ExportResult(
                format="onnx",
                path="",
                export_time=time.time() - start_time,
                success=False,
                error=str(e),
            )
    
    def _export_tensorrt(self, model: FightDetectionModel) -> ExportResult:
        """Export to TensorRT format"""
        
        logger.info("Exporting to TensorRT...")
        start_time = time.time()
        
        try:
            import tensorrt as trt
            
            output_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.engine",
            )
            
            # Create builder
            logger_ = trt.Logger(trt.Logger.WARNING)
            builder = trt.Builder(logger_)
            
            # Create network
            network = builder.create_network(
                1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH)
            )
            
            # Parse ONNX (first export to ONNX)
            onnx_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.onnx",
            )
            
            if not os.path.exists(onnx_path):
                self._export_onnx(model)
            
            parser = trt.OnnxParser(network, logger_)
            
            with open(onnx_path, 'rb') as f:
                if not parser.parse(f.read()):
                    for i in range(parser.num_errors):
                        logger.error(f"TensorRT parse error: {parser.get_error(i)}")
                    raise RuntimeError("Failed to parse ONNX")
            
            # Build config
            config = builder.create_builder_config()
            config.max_workspace_size = self.config.tensorrt_workspace_mb * (1024 * 1024)
            
            if self.config.tensorrt_fp16:
                config.set_flag(trt.BuilderFlag.FP16)
            
            if self.config.tensorrt_int8:
                config.set_flag(trt.BuilderFlag.INT8)
            
            # Build engine
            engine = builder.build_engine(network, config)
            
            if engine is None:
                raise RuntimeError("Failed to build TensorRT engine")
            
            # Serialize
            with open(output_path, 'wb') as f:
                f.write(engine.serialize())
            
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            
            return ExportResult(
                format="tensorrt",
                path=output_path,
                file_size_mb=round(file_size, 2),
                export_time=time.time() - start_time,
                success=True,
            )
            
        except ImportError:
            logger.warning("TensorRT not installed")
            return ExportResult(
                format="tensorrt",
                path="",
                export_time=0,
                success=False,
                error="TensorRT not installed",
            )
        except Exception as e:
            logger.error(f"TensorRT export failed: {e}")
            return ExportResult(
                format="tensorrt",
                path="",
                export_time=time.time() - start_time,
                success=False,
                error=str(e),
            )
    
    def _export_coreml(self, model: FightDetectionModel) -> ExportResult:
        """Export to CoreML format (Apple devices)"""
        
        logger.info("Exporting to CoreML...")
        start_time = time.time()
        
        try:
            import coremltools as ct
            
            output_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.mlmodel",
            )
            
            # First export to TorchScript
            ts_path = os.path.join(
                self.config.output_dir,
                f"{self.config.model_name}.torchscript.pt",
            )
            
            if not os.path.exists(ts_path):
                self._export_torchscript(model)
            
            ts_model = torch.jit.load(ts_path)
            
            # Convert to CoreML
            mlmodel = ct.convert(
                ts_model,
                inputs=[ct.TensorType(
                    shape=(self.config.batch_size, self.config.channels, *self.config.input_size)
                )],
            )
            
            mlmodel.save(output_path)
            
            file_size = os.path.getsize(output_path) / (1024 * 1024)
            
            return ExportResult(
                format="coreml",
                path=output_path,
                file_size_mb=round(file_size, 2),
                export_time=time.time() - start_time,
                success=True,
            )
            
        except ImportError:
            logger.warning("coremltools not installed")
            return ExportResult(
                format="coreml",
                path="",
                export_time=0,
                success=False,
                error="coremltools not installed",
            )
        except Exception as e:
            logger.error(f"CoreML export failed: {e}")
            return ExportResult(
                format="coreml",
                path="",
                export_time=time.time() - start_time,
                success=False,
                error=str(e),
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get export statistics"""
        return {
            "results": [
                {
                    "format": r.format,
                    "path": r.path,
                    "size_mb": r.file_size_mb,
                    "time": round(r.export_time, 2),
                    "success": r.success,
                }
                for r in self.results
            ],
            "total_successful": sum(1 for r in self.results if r.success),
        }