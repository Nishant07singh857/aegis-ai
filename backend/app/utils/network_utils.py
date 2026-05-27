# backend/app/utils/network_utils.py
"""
AEGIS AI - Network Utilities
HTTP requests, downloads, network diagnostics, and web helpers.
"""

import time
import asyncio
import hashlib
from typing import Dict, Optional, Any
from dataclasses import dataclass
import structlog

logger = structlog.get_logger("aegis.utils.network")


@dataclass
class DownloadResult:
    """Download result"""
    success: bool
    path: str = ""
    size_mb: float = 0.0
    duration_seconds: float = 0.0
    error: str = ""
    checksum: str = ""


class NetworkUtils:
    """Network utility functions"""
    
    @staticmethod
    async def check_connectivity(host: str = "8.8.8.8", port: int = 53, timeout: float = 3.0) -> bool:
        """Check network connectivity"""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False
    
    @staticmethod
    def get_local_ip() -> str:
        """Get local IP address"""
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
    
    @staticmethod
    async def http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> Optional[str]:
        """Simple HTTP GET request"""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=timeout) as response:
                    if response.status == 200:
                        return await response.text()
                    else:
                        logger.warning(f"HTTP {response.status} for {url}")
                        return None
        except Exception as e:
            logger.error(f"HTTP GET failed: {e}")
            return None
    
    @staticmethod
    async def http_post(
        url: str,
        data: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """Simple HTTP POST request"""
        try:
            import aiohttp
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    data=data,
                    json=json_data,
                    headers=headers,
                    timeout=timeout,
                ) as response:
                    if response.status in [200, 201]:
                        return await response.json()
                    else:
                        logger.warning(f"HTTP {response.status} for {url}")
                        return None
        except Exception as e:
            logger.error(f"HTTP POST failed: {e}")
            return None


class Downloader:
    """File downloader with progress and retry"""
    
    def __init__(self, max_retries: int = 3, chunk_size: int = 8192):
        self.max_retries = max_retries
        self.chunk_size = chunk_size
    
    async def download(self, url: str, output_path: str, expected_checksum: Optional[str] = None) -> DownloadResult:
        """Download file with retry"""
        import os
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        
        for attempt in range(self.max_retries):
            try:
                import aiohttp
                start_time = time.time()
                
                async with aiohttp.ClientSession() as session:
                    async with session.get(url) as response:
                        if response.status != 200:
                            continue
                        
                        total_size = int(response.headers.get('content-length', 0))
                        downloaded = 0
                        
                        with open(output_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(self.chunk_size):
                                f.write(chunk)
                                downloaded += len(chunk)
                        
                        duration = time.time() - start_time
                        size_mb = os.path.getsize(output_path) / (1024 * 1024)
                        
                        # Verify checksum
                        checksum = ""
                        if expected_checksum:
                            checksum = hashlib.md5(open(output_path, 'rb').read()).hexdigest()
                            if checksum != expected_checksum:
                                os.remove(output_path)
                                logger.warning(f"Checksum mismatch for {url}")
                                continue
                        
                        return DownloadResult(
                            success=True,
                            path=output_path,
                            size_mb=round(size_mb, 2),
                            duration_seconds=round(duration, 2),
                            checksum=checksum,
                        )
                        
            except Exception as e:
                logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(2 ** attempt)
        
        return DownloadResult(success=False, error=f"Failed after {self.max_retries} attempts")