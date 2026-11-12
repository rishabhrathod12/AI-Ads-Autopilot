"""Retry with backoff, Retry-After handling and a client-side rate limiter. No HTTP types here.

Amazon documents that Ads API rate limits are dynamic and that 429 responses should be expected.
It was NOT possible to confirm whether a ``Retry-After`` header is sent, so it is honored when
present and ignored when absent (exponential backoff with jitter either way).
"""

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from app.application.ads_api.errors import RateLimitedError, TransientError

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5  # total tries, including the first
    base_delay: float = 1.0
    max_delay: float = 60.0  # longest single wait; a longer Retry-After is surfaced, not slept
    jitter: float = 0.25  # +/- fraction of the delay

    def __post_init__(self) -> None:
        if self.max_attempts < 1 or self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("invalid retry policy")
        if not 0 <= self.jitter < 1:
            raise ValueError("jitter must be in [0, 1)")


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds from a ``Retry-After`` value (seconds or HTTP-date); None if absent or invalid."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    if text.isdigit():
        return float(int(text))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return float(max(0.0, (when - (now or datetime.now(UTC))).total_seconds()))


def backoff_delay(attempt: int, policy: RetryPolicy, rng: Callable[[], float]) -> float:
    """Exponential delay for the Nth retry (1-based) with symmetric jitter, capped."""
    raw = min(policy.max_delay, policy.base_delay * (2 ** (attempt - 1)))
    spread = raw * policy.jitter
    return float(max(0.0, min(policy.max_delay, raw + (rng() * 2 - 1) * spread)))


async def with_retries[T](
    operation: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Sleep = asyncio.sleep,
    rng: Callable[[], float] = random.random,
) -> T:
    """Run ``operation``; retry on RateLimitedError and TransientError, never on anything else."""
    attempt = 0
    while True:
        attempt += 1
        try:
            return await operation()
        except RateLimitedError as exc:
            if attempt >= policy.max_attempts:
                raise
            delay = backoff_delay(attempt, policy, rng)
            if exc.retry_after is not None:
                if exc.retry_after > policy.max_delay:
                    raise  # the server wants a longer pause than we will block for
                delay = max(delay, exc.retry_after)
            await sleep(delay)
        except TransientError:
            if attempt >= policy.max_attempts:
                raise
            await sleep(backoff_delay(attempt, policy, rng))


class AsyncRateLimiter:
    """Token bucket: at most ``rate`` requests per second on average, bursts up to ``burst``."""

    def __init__(
        self,
        rate: float,
        burst: int = 1,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if rate <= 0 or burst < 1:
            raise ValueError("rate must be positive and burst at least 1")
        self._rate, self._burst = rate, float(burst)
        self._tokens = float(burst)
        self._clock, self._sleep = clock, sleep
        self._last = clock()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                self._tokens = min(self._burst, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._rate)
