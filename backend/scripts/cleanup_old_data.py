# backend/scripts/cleanup_old_data.py
"""
AEGIS AI - Data Cleanup Script
Removes old match recordings, temporary files, expired caches,
and archived data beyond retention period.
"""

import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

import structlog
from app.config import settings

logger = structlog.get_logger("aegis.scripts.cleanup")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# RETENTION CONFIGURATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RETENTION_CONFIG = {
    "recordings": {"path": "recordings", "max_age_days": 30, "min_free_space_gb": 50},
    "highlights": {"path": "highlights", "max_age_days": 90, "min_free_space_gb": 10},
    "temp": {"path": "temp", "max_age_hours": 24, "min_free_space_gb": 5},
    "logs": {"path": "logs", "max_age_days": 14, "min_free_space_gb": 2},
    "exports": {"path": "exports", "max_age_days": 7, "min_free_space_gb": 10},
}


def cleanup_directory(data_dir: str, config: dict) -> dict:
    """Clean up a directory based on retention config"""
    
    full_path = os.path.join(settings.DATA_DIR, config["path"])
    
    if not os.path.exists(full_path):
        return {"path": config["path"], "files_removed": 0, "space_freed_mb": 0}
    
    files_removed = 0
    space_freed = 0
    
    # Check max age
    max_age_seconds = config.get("max_age_days", 30) * 86400
    max_age_hours = config.get("max_age_hours", 0)
    
    if max_age_hours > 0:
        max_age_seconds = max_age_hours * 3600
    
    cutoff_time = datetime.now().timestamp() - max_age_seconds
    
    for filename in os.listdir(full_path):
        filepath = os.path.join(full_path, filename)
        
        if os.path.isfile(filepath):
            file_mtime = os.path.getmtime(filepath)
            
            if file_mtime < cutoff_time:
                try:
                    file_size = os.path.getsize(filepath)
                    os.remove(filepath)
                    files_removed += 1
                    space_freed += file_size
                except Exception as e:
                    logger.warning(f"Failed to remove {filepath}: {e}")
    
    # Check minimum free space
    min_free_bytes = config.get("min_free_space_gb", 10) * (1024**3)
    
    if min_free_bytes > 0:
        # Get all files sorted by modification time (oldest first)
        all_files = []
        for filename in os.listdir(full_path):
            filepath = os.path.join(full_path, filename)
            if os.path.isfile(filepath):
                all_files.append((filepath, os.path.getmtime(filepath)))
        
        all_files.sort(key=lambda x: x[1])  # Sort by mtime
        
        # Check free space
        import shutil
        free_space = shutil.disk_usage(full_path).free
        
        for filepath, _ in all_files:
            if free_space >= min_free_bytes:
                break
            
            try:
                file_size = os.path.getsize(filepath)
                os.remove(filepath)
                files_removed += 1
                space_freed += file_size
                free_space += file_size
            except Exception:
                pass
    
    return {
        "path": config["path"],
        "files_removed": files_removed,
        "space_freed_mb": round(space_freed / (1024 * 1024), 1),
    }


def cleanup_empty_directories():
    """Remove empty directories"""
    count = 0
    
    for root, dirs, files in os.walk(settings.DATA_DIR, topdown=False):
        for dirname in dirs:
            dirpath = os.path.join(root, dirname)
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    count += 1
            except Exception:
                pass
    
    return count


async def main():
    """Main cleanup function"""
    logger.info("=" * 60)
    logger.info("AEGIS AI - Data Cleanup")
    logger.info("=" * 60)
    
    total_files = 0
    total_space = 0.0
    
    for name, config in RETENTION_CONFIG.items():
        logger.info(f"Cleaning up {name}...")
        result = cleanup_directory(settings.DATA_DIR, config)
        
        total_files += result["files_removed"]
        total_space += result["space_freed_mb"]
        
        logger.info(
            f"  {name}: removed {result['files_removed']} files, "
            f"freed {result['space_freed_mb']:.1f} MB"
        )
    
    # Remove empty directories
    empty_dirs = cleanup_empty_directories()
    
    logger.info(f"\nEmpty directories removed: {empty_dirs}")
    logger.info(f"Total files removed: {total_files}")
    logger.info(f"Total space freed: {total_space:.1f} MB")
    logger.info("=" * 60)
    logger.info("✅ Cleanup complete!")


if __name__ == "__main__":
    asyncio.run(main())