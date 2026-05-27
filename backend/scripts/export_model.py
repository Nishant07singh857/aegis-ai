# backend/scripts/export_model.py
"""
AEGIS AI - Model Export Script
Exports trained models to production formats (ONNX, TensorRT, TorchScript).
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
import torch

from app.config import settings
from app.ml.fight_detection.model import FightDetectionModel, ModelConfig
from app.ml.fight_detection.export import ModelExporter, ExportConfig

logger = structlog.get_logger("aegis.scripts.export_model")


async def export_fight_detection_model():
    """Export fight detection model"""
    
    logger.info("Exporting Fight Detection Model...")
    
    # Create model
    model_config = ModelConfig(
        backbone="cspdarknet53",
        num_classes=7,
        temporal_fusion=False,
    )
    model = FightDetectionModel(model_config)
    
    # Load checkpoint if available
    checkpoint_path = os.path.join(settings.DATA_DIR, "checkpoints", "fight_detection", "best_model.pt")
    if os.path.exists(checkpoint_path):
        checkpoint = torch.load(checkpoint_path, map_location='cpu')
        model.load_state_dict(checkpoint['model_state_dict'])
        logger.info(f"Loaded checkpoint from {checkpoint_path}")
    else:
        logger.warning("No checkpoint found, exporting untrained model")
    
    model.eval()
    
    # Export
    export_config = ExportConfig(
        input_size=(640, 640),
        export_onnx=True,
        export_torchscript=True,
        export_tensorrt=False,  # Requires GPU
        output_dir=os.path.join(settings.DATA_DIR, "exported_models"),
        model_name="aegis_fight_detection",
    )
    
    exporter = ModelExporter(export_config)
    results = exporter.export(model)
    
    for result in results:
        if result.success:
            logger.info(f"✓ Exported {result.format}: {result.path} ({result.file_size_mb:.1f}MB)")
        else:
            logger.error(f"✗ Failed to export {result.format}: {result.error}")
    
    return results


async def main():
    """Main function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Model Export")
    logger.info("=" * 60)
    
    # Ensure output directory exists
    os.makedirs(os.path.join(settings.DATA_DIR, "exported_models"), exist_ok=True)
    
    # Export models
    results = await export_fight_detection_model()
    
    success = sum(1 for r in results if r.success)
    logger.info(f"\nExport complete: {success}/{len(results)} formats successful")


if __name__ == "__main__":
    asyncio.run(main())