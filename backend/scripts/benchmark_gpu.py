# backend/scripts/benchmark_gpu.py
"""
AEGIS AI - GPU Benchmark Script
Tests GPU performance for vision inference.
Measures throughput, latency, and memory usage.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
import numpy as np
import torch


logger = structlog.get_logger("aegis.scripts.benchmark_gpu")


def check_gpu_info():
    """Print GPU information"""
    
    if not torch.cuda.is_available():
        logger.error("No GPU available!")
        return None
    
    gpu_count = torch.cuda.device_count()
    logger.info(f"GPU Count: {gpu_count}")
    
    for i in range(gpu_count):
        props = torch.cuda.get_device_properties(i)
        logger.info(f"GPU {i}: {props.name}")
        logger.info(f"  Memory: {props.total_memory / (1024**3):.1f} GB")
        logger.info(f"  CUDA Cores: {props.multi_processor_count * 128}")
        logger.info(f"  Compute Capability: {props.major}.{props.minor}")
    
    return gpu_count


def benchmark_yolo_inference():
    """Benchmark YOLO inference speed"""
    
    logger.info("\n" + "=" * 50)
    logger.info("YOLOv8 Inference Benchmark")
    logger.info("=" * 50)
    
    try:
        from ultralytics import YOLO
        
        # Load model
        model_sizes = ["n", "s", "m"]
        
        for size in model_sizes:
            model_path = f"yolov8{size}.pt"
            
            try:
                model = YOLO(model_path)
                
                if torch.cuda.is_available():
                    model.to('cuda')
                
                # Warmup
                dummy = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
                for _ in range(10):
                    model(dummy, verbose=False)
                
                # Benchmark
                torch.cuda.synchronize() if torch.cuda.is_available() else None
                
                iterations = 100
                start = time.time()
                
                for _ in range(iterations):
                    model(dummy, verbose=False)
                
                torch.cuda.synchronize() if torch.cuda.is_available() else None
                elapsed = time.time() - start
                
                fps = iterations / elapsed
                latency = (elapsed / iterations) * 1000
                
                # Memory
                if torch.cuda.is_available():
                    memory = torch.cuda.max_memory_allocated() / (1024**3)
                    torch.cuda.reset_peak_memory_stats()
                else:
                    memory = 0
                
                logger.info(f"YOLOv8{size}: {fps:.1f} FPS, {latency:.1f}ms latency, {memory:.2f}GB peak memory")
                
            except Exception as e:
                logger.warning(f"YOLOv8{size} benchmark failed: {e}")
    
    except ImportError:
        logger.warning("Ultralytics not installed")


def benchmark_ocr_inference():
    """Benchmark OCR inference speed"""
    
    logger.info("\n" + "=" * 50)
    logger.info("PaddleOCR Inference Benchmark")
    logger.info("=" * 50)
    
    try:
        from paddleocr import PaddleOCR
        
        ocr = PaddleOCR(use_angle_cls=True, lang='en', use_gpu=torch.cuda.is_available())
        
        # Warmup
        dummy = np.random.randint(0, 255, (200, 400, 3), dtype=np.uint8)
        for _ in range(5):
            ocr.ocr(dummy, cls=False)
        
        # Benchmark
        iterations = 50
        start = time.time()
        
        for _ in range(iterations):
            ocr.ocr(dummy, cls=False)
        
        elapsed = time.time() - start
        
        fps = iterations / elapsed
        latency = (elapsed / iterations) * 1000
        
        logger.info(f"PaddleOCR: {fps:.1f} FPS, {latency:.1f}ms latency")
        
    except ImportError:
        logger.warning("PaddleOCR not installed")


def benchmark_memory():
    """Benchmark GPU memory allocation"""
    
    logger.info("\n" + "=" * 50)
    logger.info("GPU Memory Benchmark")
    logger.info("=" * 50)
    
    if not torch.cuda.is_available():
        logger.warning("No GPU available for memory benchmark")
        return
    
    total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    
    logger.info(f"Total GPU Memory: {total_memory:.1f} GB")
    
    # Measure allocation speed
    sizes_mb = [100, 500, 1000, 2000]
    
    for size_mb in sizes_mb:
        elements = (size_mb * 1024 * 1024) // 4  # float32
        
        start = time.time()
        tensor = torch.randn(elements, device='cuda')
        torch.cuda.synchronize()
        alloc_time = (time.time() - start) * 1000
        
        del tensor
        torch.cuda.empty_cache()
        
        logger.info(f"  {size_mb}MB allocation: {alloc_time:.2f}ms")


async def main():
    """Main benchmark function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - GPU Benchmark")
    logger.info("=" * 60)
    
    # Check GPU
    gpu_count = check_gpu_info()
    
    if gpu_count is None:
        logger.warning("Running in CPU-only mode - performance will be limited")
    
    # Run benchmarks
    benchmark_yolo_inference()
    benchmark_ocr_inference()
    benchmark_memory()
    
    logger.info("\n" + "=" * 60)
    logger.info("Benchmark complete")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())