# backend/app/utils/file_utils.py
"""
AEGIS AI - File System Utilities
File operations, directory management, and file watching.
"""

import os
import json
import time
from typing import List, Optional, Any, Union
import structlog

logger = structlog.get_logger("aegis.utils.file")


class FileUtils:
    """File system utility functions"""
    
    @staticmethod
    def ensure_dir(path: str) -> str:
        """Create directory if it doesn't exist"""
        os.makedirs(path, exist_ok=True)
        return path
    
    @staticmethod
    def safe_write(path: str, data: Union[str, bytes], mode: str = "w") -> bool:
        """Safely write file (atomic write)"""
        try:
            FileUtils.ensure_dir(os.path.dirname(path))
            tmp_path = path + ".tmp"
            
            with open(tmp_path, mode) as f:
                f.write(data)
            
            os.replace(tmp_path, path)
            return True
            
        except Exception as e:
            logger.error(f"Failed to write file {path}: {e}")
            return False
    
    @staticmethod
    def safe_read(path: str, default: Any = None) -> Optional[str]:
        """Safely read file"""
        try:
            with open(path, 'r') as f:
                return f.read()
        except FileNotFoundError:
            return default
        except Exception as e:
            logger.error(f"Failed to read file {path}: {e}")
            return default
    
    @staticmethod
    def read_json(path: str, default: Any = None) -> Any:
        """Read JSON file"""
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read JSON {path}: {e}")
            return default
    
    @staticmethod
    def write_json(path: str, data: Any, indent: int = 2) -> bool:
        """Write JSON file"""
        try:
            FileUtils.ensure_dir(os.path.dirname(path))
            with open(path, 'w') as f:
                json.dump(data, f, indent=indent, ensure_ascii=False)
            return True
        except Exception as e:
            logger.error(f"Failed to write JSON {path}: {e}")
            return False
    
    @staticmethod
    def list_files(directory: str, pattern: str = "*", recursive: bool = False) -> List[str]:
        """List files matching pattern"""
        import glob
        
        if recursive:
            pattern = os.path.join(directory, "**", pattern)
        else:
            pattern = os.path.join(directory, pattern)
        
        return glob.glob(pattern, recursive=recursive)
    
    @staticmethod
    def get_size(path: str) -> int:
        """Get file/directory size in bytes"""
        if os.path.isfile(path):
            return os.path.getsize(path)
        
        total = 0
        for dirpath, dirnames, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
        return total
    
    @staticmethod
    def get_size_mb(path: str) -> float:
        """Get file/directory size in MB"""
        return FileUtils.get_size(path) / (1024 * 1024)
    
    @staticmethod
    def cleanup_old_files(directory: str, max_age_hours: float = 24) -> int:
        """Remove files older than specified hours"""
        count = 0
        cutoff = time.time() - max_age_hours * 3600
        
        for filename in os.listdir(directory):
            filepath = os.path.join(directory, filename)
            if os.path.isfile(filepath):
                if os.path.getmtime(filepath) < cutoff:
                    os.remove(filepath)
                    count += 1
        
        return count
    
    @staticmethod
    def copy_with_progress(src: str, dst: str, chunk_size: int = 8192) -> bool:
        """Copy file with progress"""
        try:
            total = os.path.getsize(src)
            copied = 0
            
            with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
                while True:
                    chunk = fsrc.read(chunk_size)
                    if not chunk:
                        break
                    fdst.write(chunk)
                    copied += len(chunk)
            
            return True
        except Exception as e:
            logger.error(f"Failed to copy {src}: {e}")
            return False


class FileWatcher:
    """Simple file watcher for monitoring changes"""
    
    def __init__(self, path: str, callback: callable):
        self.path = path
        self.callback = callback
        self._last_mtime = 0
    
    def check(self) -> bool:
        """Check if file has changed"""
        if os.path.exists(self.path):
            mtime = os.path.getmtime(self.path)
            if mtime > self._last_mtime:
                self._last_mtime = mtime
                self.callback(self.path)
                return True
        return False