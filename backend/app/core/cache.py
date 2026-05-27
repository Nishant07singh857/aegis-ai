# backend/app/core/cache.py
"""
AEGIS AI - Cache Manager
Redis-based caching with TTL, namespacing, and cache warming
"""

import asyncio
import json
from typing import Optional, Any, Dict
from functools import wraps
import hashlib

import redis.asyncio as redis
import structlog

from ..config import settings

logger = structlog.get_logger("aegis.cache")

class CacheManager:
    """Redis cache manager with advanced features"""
    
    def __init__(self):
        self._redis: Optional[redis.Redis] = None
        self._namespace = "aegis"
        self._default_ttl = 300  # 5 minutes
        
        # Cache statistics
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "deletes": 0,
        }
    
    async def connect(self):
        """Connect to Redis"""
        self._redis = redis.Redis.from_url(
            settings.REDIS_URL,
            max_connections=10,
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info("Cache manager connected")
    
    async def disconnect(self):
        """Disconnect from Redis"""
        if self._redis:
            await self._redis.close()
    
    def _make_key(self, key: str) -> str:
        """Create namespaced cache key"""
        return f"{self._namespace}:cache:{key}"
    
    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        try:
            value = await self._redis.get(self._make_key(key))
            if value:
                self._stats["hits"] += 1
                return json.loads(value)
            self._stats["misses"] += 1
            return None
        except Exception as e:
            logger.error(f"Cache get error: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set value in cache with optional TTL"""
        try:
            serialized = json.dumps(value, default=str)
            ttl = ttl or self._default_ttl
            
            await self._redis.setex(
                self._make_key(key),
                ttl,
                serialized,
            )
            self._stats["sets"] += 1
            return True
        except Exception as e:
            logger.error(f"Cache set error: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Delete value from cache"""
        try:
            await self._redis.delete(self._make_key(key))
            self._stats["deletes"] += 1
            return True
        except Exception as e:
            logger.error(f"Cache delete error: {e}")
            return False
    
    async def exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        return await self._redis.exists(self._make_key(key)) > 0
    
    async def increment(self, key: str, amount: int = 1) -> int:
        """Increment a counter in cache"""
        return await self._redis.incrby(self._make_key(key), amount)
    
    async def get_or_set(
        self,
        key: str,
        factory: callable,
        ttl: Optional[int] = None,
    ) -> Any:
        """Get from cache or compute and set"""
        value = await self.get(key)
        if value is not None:
            return value
        
        # Compute value
        if asyncio.iscoroutinefunction(factory):
            value = await factory()
        else:
            value = factory()
        
        # Cache it
        await self.set(key, value, ttl)
        return value
    
    def cached(self, ttl: Optional[int] = None, key_prefix: str = ""):
        """Decorator for caching function results"""
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                # Generate cache key
                key_parts = [key_prefix, func.__name__]
                key_parts.extend([str(arg) for arg in args])
                key_parts.extend([f"{k}:{v}" for k, v in sorted(kwargs.items())])
                cache_key = hashlib.md5(
                    ":".join(key_parts).encode()
                ).hexdigest()
                
                return await self.get_or_set(cache_key, lambda: func(*args, **kwargs), ttl)
            return wrapper
        return decorator
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total = self._stats["hits"] + self._stats["misses"]
        hit_rate = self._stats["hits"] / total if total > 0 else 0
        
        return {
            **self._stats,
            "hit_rate": round(hit_rate, 4),
            "total_operations": total,
        }


# Global cache instance
cache_manager = CacheManager()