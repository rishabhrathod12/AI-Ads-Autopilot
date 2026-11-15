"""A small in-process cache for read-only analytics responses.

Why: the heavy routes aggregate hundreds of thousands of rows, so a handful of simultaneous users
waits seconds while the database is saturated (docs/OPERATIONS.md, load test). Most of those
requests ask for the same thing.

Why it cannot show stale data: every key includes the organization and the reader's *data version*
(``PerformanceReader.data_version``), which changes the moment a sync ingests anything, so a
cached result is only ever served for data identical to what it was computed from. The
time-to-live is a second, independent safety net, not what keeps results correct.

Why it cannot cross tenants: the organization id is part of every key, and the cache is consulted
only after authentication and role checks, inside a route that already holds that tenant's session.

Concurrency: identical requests arriving together share one computation (single flight). If the
request doing the work fails or is cancelled (a client disconnecting closes its database session),
the others compute for themselves instead of inheriting its failure. Errors are never cached.

Per process and in memory (one backend instance today; see docs/OPERATIONS.md). Entries are copied
on the way out so a caller cannot change what later callers receive.
"""

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

log = structlog.get_logger()
Key = tuple[Any, ...]


class ResultCache:
    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._entries: OrderedDict[Key, tuple[float, Any]] = OrderedDict()
        self._inflight: dict[Key, asyncio.Future[Any]] = {}
        self.hits = 0
        self.misses = 0
        self.shared = 0  # requests that waited for another request's computation

    @property
    def enabled(self) -> bool:
        return self._max > 0 and self._ttl > 0

    def __len__(self) -> int:
        return len(self._entries)

    def _lookup(self, key: Key) -> tuple[bool, Any]:
        found = self._entries.get(key)
        if found is None:
            return False, None
        stored_at, value = found
        if self._clock() - stored_at >= self._ttl:
            del self._entries[key]
            return False, None
        self._entries.move_to_end(key)
        return True, value

    def _store(self, key: Key, value: Any) -> None:
        self._entries[key] = (self._clock(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max:
            self._entries.popitem(last=False)

    async def get_or_compute(self, key: Key, compute: Callable[[], Awaitable[Any]]) -> Any:
        if not self.enabled:
            return await compute()
        while True:
            hit, value = self._lookup(key)
            if hit:
                self.hits += 1
                return copy.deepcopy(value)
            waiting = self._inflight.get(key)
            if waiting is None:
                break
            self.shared += 1
            try:
                return copy.deepcopy(await asyncio.shield(waiting))
            except asyncio.CancelledError:
                raise
            except Exception:
                # The request doing the work failed: look again, then compute ourselves.
                log.info("cache_leader_failed")
                continue
        self.misses += 1
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            value = await compute()
        except BaseException as exc:
            future.set_exception(exc if isinstance(exc, Exception) else RuntimeError("cancelled"))
            future.exception()  # mark retrieved: nobody may be waiting
            raise
        else:
            self._store(key, value)
            future.set_result(value)
            return copy.deepcopy(value)
        finally:
            self._inflight.pop(key, None)
