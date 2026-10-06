"""Concurrency semaphore + tokens-per-minute bucket (docs/LLM.md §8).

`slot(tokens)` waits for a free concurrency slot, then for enough tokens in the bucket (capacity =
TPM, refilled continuously). Requests larger than the capacity wait for a full bucket instead of
forever. Clock and sleep are injectable for tests.
"""

import asyncio
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager


class TokenBucket:
    def __init__(
        self,
        tokens_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.capacity = float(tokens_per_minute)
        self._rate = tokens_per_minute / 60.0
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._updated = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self._rate)
        self._updated = now

    async def acquire(self, tokens: int) -> None:
        wanted = min(float(tokens), self.capacity)
        async with self._lock:  # FIFO: one waiter at a time
            self._refill()
            while self._tokens < wanted:
                await self._sleep((wanted - self._tokens) / self._rate)
                self._refill()
            self._tokens -= wanted


class RateLimiter:
    def __init__(
        self, max_concurrency: int, tokens_per_minute: int, bucket: TokenBucket | None = None
    ) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self.bucket = bucket or TokenBucket(tokens_per_minute)

    @asynccontextmanager
    async def slot(self, tokens: int) -> AsyncGenerator[None]:
        async with self._semaphore:
            await self.bucket.acquire(tokens)
            yield
