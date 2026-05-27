# backend/app/utils/async_utils.py
"""
AEGIS AI - Async Utilities
Async helpers, rate limiting, circuit breaker, and concurrency tools.
"""

import asyncio
import time
from typing import List, Any, Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
import structlog

logger = structlog.get_logger("aegis.utils.async")


class CircuitState(str, Enum):
    """Circuit breaker states"""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"          # Failing, rejecting requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class RateLimiter:
    """Token bucket rate limiter"""
    rate: float  # Requests per second
    burst: int = 10
    _tokens: float = field(default=0.0)
    _last_refill: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    
    async def acquire(self) -> bool:
        """Acquire a token, returns True if allowed"""
        async with self._lock:
            now = time.time()
            elapsed = now - self._last_refill
            self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
            self._last_refill = now
            
            if self._tokens >= 1:
                self._tokens -= 1
                return True
            return False
    
    async def wait(self) -> None:
        """Wait until a token is available"""
        while not await self.acquire():
            await asyncio.sleep(1 / self.rate)


class CircuitBreaker:
    """Circuit breaker for fault tolerance"""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_requests: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_requests = half_open_max_requests
        
        self.state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._half_open_requests = 0
        self._lock = asyncio.Lock()
    
    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Call function with circuit breaker protection"""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if time.time() - self._last_failure_time > self.recovery_timeout:
                    self.state = CircuitState.HALF_OPEN
                    self._half_open_requests = 0
                    logger.info("Circuit breaker: half-open")
                else:
                    raise Exception("Circuit breaker is OPEN")
            
            if self.state == CircuitState.HALF_OPEN:
                if self._half_open_requests >= self.half_open_max_requests:
                    raise Exception("Circuit breaker: too many half-open requests")
                self._half_open_requests += 1
        
        try:
            result = await func(*args, **kwargs)
            
            async with self._lock:
                if self.state == CircuitState.HALF_OPEN:
                    self.state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info("Circuit breaker: closed (recovered)")
            
            return result
            
        except Exception as e:
            async with self._lock:
                self._failure_count += 1
                self._last_failure_time = time.time()
                
                if self._failure_count >= self.failure_threshold:
                    self.state = CircuitState.OPEN
                    logger.warning(f"Circuit breaker: OPEN ({self._failure_count} failures)")
            
            raise e


class AsyncUtils:
    """Async utility functions"""
    
    @staticmethod
    async def gather_with_timeout(
        coros: List[Coroutine],
        timeout: float = 30.0,
        return_exceptions: bool = True,
    ) -> List[Any]:
        """Gather coroutines with timeout"""
        try:
            return await asyncio.wait_for(
                asyncio.gather(*coros, return_exceptions=return_exceptions),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(f"Gather timed out after {timeout}s")
            return [asyncio.TimeoutError()] * len(coros)
    
    @staticmethod
    async def run_with_retry(
        func: Callable,
        max_retries: int = 3,
        delay: float = 1.0,
        backoff: float = 2.0,
        exceptions: tuple = (Exception,),
    ) -> Any:
        """Run async function with retry"""
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return await func()
            except exceptions as e:
                last_exception = e
                if attempt < max_retries - 1:
                    wait = delay * (backoff ** attempt)
                    logger.warning(f"Retry {attempt + 1}/{max_retries} in {wait:.1f}s: {e}")
                    await asyncio.sleep(wait)
        
        raise last_exception
    
    @staticmethod
    async def throttle(
        func: Callable,
        min_interval: float = 1.0,
    ) -> Callable:
        """Throttle function calls"""
        last_called = 0.0
        
        async def wrapper(*args, **kwargs):
            nonlocal last_called
            now = time.time()
            wait = max(0, min_interval - (now - last_called))
            if wait > 0:
                await asyncio.sleep(wait)
            last_called = time.time()
            return await func(*args, **kwargs)
        
        return wrapper
    
    @staticmethod
    def create_task_safe(coro: Coroutine, name: str = "") -> asyncio.Task:
        """Create task with error handling"""
        async def wrapper():
            try:
                await coro
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Task '{name}' failed: {e}", exc_info=True)
        
        return asyncio.create_task(wrapper(), name=name)
    
    @staticmethod
    async def sleep_precise(seconds: float) -> None:
        """More precise async sleep"""
        start = time.perf_counter()
        while time.perf_counter() - start < seconds:
            await asyncio.sleep(0.001)
    
    @staticmethod
    class AsyncLockManager:
        """Context manager for async locks with timeout"""
        
        def __init__(self, lock: asyncio.Lock, timeout: float = 5.0):
            self.lock = lock
            self.timeout = timeout
        
        async def __aenter__(self):
            try:
                await asyncio.wait_for(self.lock.acquire(), timeout=self.timeout)
                return self.lock
            except asyncio.TimeoutError:
                raise TimeoutError(f"Lock acquisition timed out after {self.timeout}s")
        
        async def __aexit__(self, *args):
            self.lock.release()