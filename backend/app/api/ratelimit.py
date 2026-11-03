"""A small in-process sliding-window limiter, keyed by tenant.

Per process only: with several workers each has its own window, so the effective limit is higher.
The spend caps in the database are the hard cost control; this stops a tight loop of requests.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from uuid import UUID


class RateLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[UUID, deque[float]] = defaultdict(deque)

    def allow(self, key: UUID) -> bool:
        now = self._clock()
        hits = self._hits[key]
        while hits and now - hits[0] >= self._window:
            hits.popleft()
        if len(hits) >= self._limit:
            return False
        hits.append(now)
        return True
