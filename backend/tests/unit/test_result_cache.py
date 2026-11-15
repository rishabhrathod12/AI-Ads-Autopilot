"""The analytics result cache: correctness first, then concurrency and failure handling."""

import asyncio
from typing import Any

import pytest

from app.api.cache import ResultCache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Counter:
    def __init__(self, value: Any = None) -> None:
        self.calls = 0
        self.value = {"rows": [1, 2, 3]} if value is None else value

    async def __call__(self) -> Any:
        self.calls += 1
        return self.value


async def test_a_repeated_key_is_computed_once() -> None:
    cache, compute = ResultCache(), Counter()
    first = await cache.get_or_compute(("org", "route", (1,), "v1"), compute)
    second = await cache.get_or_compute(("org", "route", (1,), "v1"), compute)
    assert first == second == {"rows": [1, 2, 3]} and compute.calls == 1
    assert (cache.hits, cache.misses) == (1, 1)


async def test_callers_get_copies_so_one_cannot_change_what_another_receives() -> None:
    cache, compute = ResultCache(), Counter()
    key = ("org", "route", (), "v1")
    first = await cache.get_or_compute(key, compute)
    first["rows"].append(99)  # the caller mutates its copy
    first["extra"] = True
    assert await cache.get_or_compute(key, compute) == {"rows": [1, 2, 3]}
    assert compute.calls == 1


async def test_any_difference_in_the_key_is_a_separate_entry() -> None:
    cache, compute = ResultCache(), Counter()
    keys = [
        ("org-a", "route", (1,), "v1"),
        ("org-b", "route", (1,), "v1"),  # another organization
        ("org-a", "other", (1,), "v1"),  # another route
        ("org-a", "route", (2,), "v1"),  # other parameters
        ("org-a", "route", (1,), "v2"),  # new data arrived
    ]
    for key in keys:
        await cache.get_or_compute(key, compute)
    assert compute.calls == len(keys) and len(cache) == len(keys)


async def test_entries_expire_after_the_time_to_live() -> None:
    clock, compute = Clock(), Counter()
    cache = ResultCache(ttl_seconds=60, clock=clock)
    key = ("org", "route", (), "v1")
    await cache.get_or_compute(key, compute)
    clock.now += 59.9
    await cache.get_or_compute(key, compute)
    assert compute.calls == 1
    clock.now += 0.2
    await cache.get_or_compute(key, compute)
    assert compute.calls == 2


async def test_the_least_recently_used_entry_is_evicted_first() -> None:
    cache, compute = ResultCache(max_entries=2), Counter()
    a, b, c = (("o", "r", (n,), "v") for n in "abc")
    await cache.get_or_compute(a, compute)
    await cache.get_or_compute(b, compute)
    await cache.get_or_compute(a, compute)  # a is now the most recently used
    await cache.get_or_compute(c, compute)  # evicts b
    assert len(cache) == 2
    calls = compute.calls
    await cache.get_or_compute(a, compute)
    assert compute.calls == calls  # a survived
    await cache.get_or_compute(b, compute)
    assert compute.calls == calls + 1  # b did not


@pytest.mark.parametrize("kwargs", [{"ttl_seconds": 0}, {"max_entries": 0}])
async def test_disabled_cache_always_computes(kwargs: dict[str, float]) -> None:
    cache, compute = ResultCache(**kwargs), Counter()  # type: ignore[arg-type]
    for _ in range(3):
        await cache.get_or_compute(("o", "r", (), "v"), compute)
    assert compute.calls == 3 and not cache.enabled and len(cache) == 0


async def test_errors_are_never_cached() -> None:
    cache = ResultCache()
    key = ("o", "r", (), "v")
    attempts = 0

    async def flaky() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError("no data yet")
        return "ok"

    with pytest.raises(ValueError, match="no data yet"):
        await cache.get_or_compute(key, flaky)
    assert await cache.get_or_compute(key, flaky) == "ok" and attempts == 2


async def test_simultaneous_identical_requests_share_one_computation() -> None:
    cache = ResultCache()
    key = ("o", "r", (), "v")
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow() -> dict[str, int]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"n": 1}

    leader = asyncio.create_task(cache.get_or_compute(key, slow))
    await started.wait()
    waiters = [asyncio.create_task(cache.get_or_compute(key, slow)) for _ in range(9)]
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(leader, *waiters)
    assert calls == 1 and all(r == {"n": 1} for r in results)
    assert cache.shared == 9 and cache.misses == 1
    results[0]["n"] = 5  # still copies
    assert results[1] == {"n": 1}


async def test_waiters_compute_for_themselves_when_the_leader_fails() -> None:
    cache = ResultCache()
    key = ("o", "r", (), "v")
    started, release = asyncio.Event(), asyncio.Event()

    async def failing() -> str:
        started.set()
        await release.wait()
        raise RuntimeError("leader's database session died")

    async def healthy() -> str:
        return "mine"

    leader = asyncio.create_task(cache.get_or_compute(key, failing))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_compute(key, healthy))
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(RuntimeError):
        await leader
    assert await waiter == "mine"  # not the leader's failure


async def test_waiters_compute_for_themselves_when_the_leader_is_cancelled() -> None:
    cache = ResultCache()
    key = ("o", "r", (), "v")
    started = asyncio.Event()

    async def forever() -> str:
        started.set()
        await asyncio.sleep(3600)
        return "never"

    async def healthy() -> str:
        return "mine"

    leader = asyncio.create_task(cache.get_or_compute(key, forever))
    await started.wait()
    waiter = asyncio.create_task(cache.get_or_compute(key, healthy))
    await asyncio.sleep(0)
    leader.cancel()  # the client disconnected
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert await waiter == "mine"
    assert len(cache) == 1  # the waiter's own result is what got cached
