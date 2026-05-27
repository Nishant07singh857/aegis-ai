# backend/scripts/validate_config.py
"""
AEGIS AI - Configuration Validation Script
Validates all configuration files, environment variables,
and system requirements before deployment.
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.config import settings

logger = structlog.get_logger("aegis.scripts.validate_config")


def validate_environment():
    """Validate environment variables"""
    
    logger.info("Validating environment...")
    errors = []
    
    # Required settings
    required_settings = [
        ("SECRET_KEY", settings.SECRET_KEY),
        ("DATABASE_URL", settings.DATABASE_URL),
        ("REDIS_URL", settings.REDIS_URL),
    ]
    
    for name, value in required_settings:
        if not value or value == "change-me-in-production-please":
            errors.append(f"❌ {name} is not set or using default value")
        else:
            logger.info(f"  ✓ {name}: configured")
    
    return errors


def validate_database_url():
    """Validate database URL format"""
    
    logger.info("Validating database connection...")
    
    url = settings.DATABASE_URL
    
    if not url:
        return ["❌ DATABASE_URL is empty"]
    
    # Check format
    if not url.startswith(("postgresql://", "postgresql+asyncpg://")):
        return ["❌ DATABASE_URL must start with postgresql:// or postgresql+asyncpg://"]
    
    logger.info("  ✓ Database URL format valid")
    return []


def validate_redis_url():
    """Validate Redis URL format"""
    
    logger.info("Validating Redis connection...")
    
    url = settings.REDIS_URL
    
    if not url:
        return ["❌ REDIS_URL is empty"]
    
    if not url.startswith("redis://"):
        return ["❌ REDIS_URL must start with redis://"]
    
    logger.info("  ✓ Redis URL format valid")
    return []


def validate_paths():
    """Validate required paths exist"""
    
    logger.info("Validating paths...")
    errors = []
    
    required_dirs = [
        settings.DATA_DIR,
        settings.MODELS_DIR,
        settings.LOGS_DIR,
    ]
    
    for dir_path in required_dirs:
        if not os.path.exists(dir_path):
            try:
                os.makedirs(dir_path, exist_ok=True)
                logger.info(f"  ✓ Created directory: {dir_path}")
            except Exception as e:
                errors.append(f"❌ Cannot create directory {dir_path}: {e}")
        else:
            logger.info(f"  ✓ Directory exists: {dir_path}")
    
    return errors


def validate_gpu():
    """Validate GPU availability if required"""
    
    logger.info("Validating GPU...")
    
    try:
        import torch
        
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            logger.info(f"  ✓ GPU available: {gpu_name} ({memory:.1f} GB)")
        else:
            logger.warning("  ⚠ No GPU detected - running in CPU mode")
    except ImportError:
        logger.warning("  ⚠ PyTorch not installed - GPU check skipped")
    
    return []


def validate_dependencies():
    """Validate critical dependencies"""
    
    logger.info("Validating dependencies...")
    errors = []
    
    critical_packages = [
        ("fastapi", "FastAPI"),
        ("sqlalchemy", "SQLAlchemy"),
        ("redis", "Redis"),
        ("numpy", "NumPy"),
        ("cv2", "OpenCV"),
    ]
    
    optional_packages = [
        ("torch", "PyTorch"),
        ("ultralytics", "Ultralytics (YOLO)"),
        ("paddleocr", "PaddleOCR"),
    ]
    
    for module_name, display_name in critical_packages:
        try:
            __import__(module_name)
            logger.info(f"  ✓ {display_name}")
        except ImportError:
            errors.append(f"❌ {display_name} not installed")
    
    for module_name, display_name in optional_packages:
        try:
            __import__(module_name)
            logger.info(f"  ✓ {display_name} (optional)")
        except ImportError:
            logger.warning(f"  ⚠ {display_name} not installed (optional)")
    
    return errors


async def main():
    """Main validation function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Configuration Validation")
    logger.info("=" * 60)
    
    all_errors = []
    
    # Run validations
    all_errors.extend(validate_environment())
    all_errors.extend(validate_database_url())
    all_errors.extend(validate_redis_url())
    all_errors.extend(validate_paths())
    validate_gpu()
    all_errors.extend(validate_dependencies())
    
    # Print summary
    logger.info("\n" + "=" * 60)
    
    if all_errors:
        logger.error(f"❌ Validation failed with {len(all_errors)} errors:")
        for error in all_errors:
            logger.error(f"  {error}")
        sys.exit(1)
    else:
        logger.info("✅ All validations passed!")
        logger.info(f"   Environment: {settings.ENVIRONMENT.value}")
        logger.info(f"   Debug Mode: {settings.DEBUG}")
        logger.info(f"   API: http://{settings.HOST}:{settings.PORT}")
    
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())