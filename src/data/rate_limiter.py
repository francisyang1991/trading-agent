"""
Rate Limiter - Token bucket implementation for IBKR API rate limiting.

IBKR enforces a 50 messages/second limit. This module provides:
- Token bucket algorithm for smooth rate limiting
- Priority queues for different request types
- Backpressure handling when approaching limits

Usage:
    from src.data.rate_limiter import RateLimiter, Priority
    
    limiter = RateLimiter(rate=45, capacity=50)  # Leave some headroom
    
    # Acquire tokens before making API calls
    await limiter.acquire(Priority.CRITICAL)  # For risk/emergency orders
    await limiter.acquire(Priority.TRADING)   # For normal trading
    await limiter.acquire(Priority.DATA)      # For market data
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Dict, Optional
from loguru import logger


class Priority(IntEnum):
    """
    Request priority levels.
    
    Lower numbers = higher priority.
    """
    CRITICAL = 0   # Risk orders, emergency liquidation
    TRADING = 1    # Normal trade execution
    DATA = 2       # Real-time market data
    BACKGROUND = 3 # Historical data, reports


@dataclass
class RateLimiterStats:
    """Statistics for rate limiter monitoring."""
    total_requests: int = 0
    total_waited: float = 0.0
    tokens_denied: int = 0
    peak_queue_size: int = 0
    requests_by_priority: Dict[int, int] = field(default_factory=lambda: {p: 0 for p in Priority})


class RateLimiter:
    """
    Token bucket rate limiter with priority queues.
    
    Implements the token bucket algorithm to enforce rate limits:
    - Tokens are added at a constant rate (up to capacity)
    - Each request consumes one or more tokens
    - If no tokens available, request waits or is rejected
    
    Priority queues ensure critical operations (risk/trading) are
    processed before background operations (data fetching).
    """
    
    def __init__(
        self,
        rate: float = 45.0,      # Tokens per second (IBKR limit is 50)
        capacity: int = 50,       # Maximum burst capacity
        min_tokens: int = 5,      # Reserve tokens for critical operations
    ):
        """
        Initialize rate limiter.
        
        Args:
            rate: Token replenishment rate per second
            capacity: Maximum token bucket size
            min_tokens: Minimum tokens reserved for CRITICAL priority
        """
        self.rate = rate
        self.capacity = capacity
        self.min_tokens = min_tokens
        
        # Token bucket state
        self._tokens = float(capacity)
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()
        
        # Priority queues
        self._queues: Dict[Priority, asyncio.Queue] = {
            p: asyncio.Queue() for p in Priority
        }
        self._queue_events: Dict[Priority, asyncio.Event] = {
            p: asyncio.Event() for p in Priority
        }
        
        # Statistics
        self.stats = RateLimiterStats()
        
        # Background processor task
        self._processor_task: Optional[asyncio.Task] = None
        self._shutdown = False
    
    def _refill_tokens(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_update
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_update = now
    
    async def acquire(
        self,
        priority: Priority = Priority.DATA,
        cost: int = 1,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Acquire tokens for a request.
        
        Args:
            priority: Request priority level
            cost: Number of tokens to consume
            timeout: Maximum wait time (None = wait forever)
            
        Returns:
            True if tokens acquired, False if timeout/denied
        """
        start_time = time.monotonic()
        
        async with self._lock:
            self._refill_tokens()
            
            # Check if we can proceed immediately
            available = self._tokens
            if priority == Priority.CRITICAL:
                # Critical always gets tokens
                can_proceed = available >= cost
            elif priority == Priority.TRADING:
                # Trading can use all but min_tokens
                can_proceed = available >= cost + self.min_tokens
            else:
                # Background needs extra headroom
                can_proceed = available >= cost + self.min_tokens * 2
            
            if can_proceed:
                self._tokens -= cost
                self.stats.total_requests += 1
                self.stats.requests_by_priority[priority] += 1
                return True
        
        # Need to wait for tokens
        if timeout == 0:
            self.stats.tokens_denied += 1
            return False
        
        # Calculate wait time
        async with self._lock:
            self._refill_tokens()
            tokens_needed = cost - (self._tokens - self.min_tokens if priority != Priority.CRITICAL else self._tokens)
            wait_time = max(0, tokens_needed / self.rate)
        
        if timeout is not None and wait_time > timeout:
            self.stats.tokens_denied += 1
            return False
        
        # Wait for tokens
        await asyncio.sleep(wait_time)
        
        # Try again
        async with self._lock:
            self._refill_tokens()
            self._tokens -= cost
            self.stats.total_requests += 1
            self.stats.requests_by_priority[priority] += 1
            self.stats.total_waited += time.monotonic() - start_time
        
        return True
    
    def try_acquire(self, priority: Priority = Priority.DATA, cost: int = 1) -> bool:
        """
        Try to acquire tokens without waiting.
        
        Args:
            priority: Request priority level
            cost: Number of tokens to consume
            
        Returns:
            True if tokens acquired immediately, False otherwise
        """
        # Note: This is a sync method, use in sync contexts only
        self._refill_tokens()
        
        available = self._tokens
        if priority == Priority.CRITICAL:
            can_proceed = available >= cost
        elif priority == Priority.TRADING:
            can_proceed = available >= cost + self.min_tokens
        else:
            can_proceed = available >= cost + self.min_tokens * 2
        
        if can_proceed:
            self._tokens -= cost
            self.stats.total_requests += 1
            self.stats.requests_by_priority[priority] += 1
            return True
        
        self.stats.tokens_denied += 1
        return False
    
    @property
    def available_tokens(self) -> float:
        """Get current available tokens."""
        self._refill_tokens()
        return self._tokens
    
    @property
    def utilization(self) -> float:
        """Get current utilization (0.0 to 1.0)."""
        return 1.0 - (self.available_tokens / self.capacity)
    
    def get_status(self) -> Dict:
        """Get rate limiter status for monitoring."""
        self._refill_tokens()
        return {
            "available_tokens": round(self._tokens, 2),
            "capacity": self.capacity,
            "rate": self.rate,
            "utilization": round(self.utilization * 100, 1),
            "total_requests": self.stats.total_requests,
            "total_waited_seconds": round(self.stats.total_waited, 2),
            "tokens_denied": self.stats.tokens_denied,
            "requests_by_priority": {
                Priority(p).name: count 
                for p, count in self.stats.requests_by_priority.items()
            }
        }
    
    def reset(self):
        """Reset rate limiter to full capacity."""
        self._tokens = float(self.capacity)
        self._last_update = time.monotonic()
        self.stats = RateLimiterStats()


class ThrottledAPIClient:
    """
    Wrapper that adds rate limiting to any API client.
    
    Usage:
        from src.data.rate_limiter import ThrottledAPIClient, Priority
        
        client = ThrottledAPIClient(ib_client, rate_limiter)
        
        # All calls are automatically rate limited
        await client.call(ib.reqAccountSummary, priority=Priority.DATA)
    """
    
    def __init__(self, client, limiter: RateLimiter):
        """
        Initialize throttled client.
        
        Args:
            client: The underlying API client
            limiter: Rate limiter instance
        """
        self.client = client
        self.limiter = limiter
    
    async def call(
        self,
        method,
        *args,
        priority: Priority = Priority.DATA,
        cost: int = 1,
        timeout: Optional[float] = 30.0,
        **kwargs
    ):
        """
        Make a rate-limited API call.
        
        Args:
            method: Method to call on the client
            *args: Positional arguments
            priority: Request priority
            cost: Token cost
            timeout: Maximum wait time for tokens
            **kwargs: Keyword arguments
            
        Returns:
            Result of the method call
            
        Raises:
            TimeoutError: If tokens not available within timeout
        """
        acquired = await self.limiter.acquire(priority, cost, timeout)
        
        if not acquired:
            raise TimeoutError(f"Rate limit timeout for {method.__name__}")
        
        try:
            result = method(*args, **kwargs)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as e:
            logger.error(f"API call failed: {method.__name__}: {e}")
            raise


# Singleton instance for shared rate limiting
_default_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Get or create the default rate limiter instance."""
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = RateLimiter()
    return _default_limiter


def set_rate_limiter(limiter: RateLimiter):
    """Set the default rate limiter instance."""
    global _default_limiter
    _default_limiter = limiter
