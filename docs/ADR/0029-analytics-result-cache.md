# ADR 0029: In-process analytics result cache keyed by data version

Status: Implemented 2026-11-15 as the next performance step after the load test (docs/OPERATIONS.md). Tested (11 unit, 14 route-level, 2 PostgreSQL);
measured once on one machine; not run from a container.

## Context
The load test showed the keyword routes saturate the database: keyword findings handled about 1 request per second and took 19 s at 20 concurrent
clients. A covering index helped only about 19%. Most simultaneous requests ask for the same thing, and data changes only when a sync ingests it.

## Decision
- Cache the eight read-only analytics routes in memory (`app/api/cache.py`), after authentication, the role check and the tenant session.
- **Key = organization, route, parameters, data version.** `PerformanceReader.data_version()` returns a token built from the organization's ingestion
  runs (count, newest start, rows ingested, newest ingestion time), which any sync changes; it is read from the small `report_runs` table, not the
  metrics. A time-to-live (default 300 s) and a size limit (256 entries, least recently used evicted) are secondary bounds.
- **Single flight:** identical concurrent requests share one computation; a failed or cancelled leader makes the others compute for themselves.
  Errors are never cached; every caller gets a copy.
- **Off switch:** `ANALYTICS_CACHE_TTL_SECONDS=0` (or max entries 0).
- Hit, miss and shared counts are exposed on `/metrics`.

## Consequences
- Same load script, same data, cache on: keyword findings p50 at 20 clients 19 s to 0.41 s; mixed traffic 2.7 to 55.8 requests per second; no errors.
  This is the best case (every client sent identical requests); a first request with new parameters still takes the full 1.5-2.5 s.
- Correctness rests on `data_version` changing whenever any data a route reads changes. That holds while all writes go through the sync service,
  which always records an ingestion run. **A future write path that changes metrics, campaigns, bids or targets without a run would be served stale
  until the TTL expires**; such a path must also bump the token (or clear the cache).
- Per process and in memory: with more than one backend instance each has its own cache (still correct, since the token comes from the database).
- Not cached: forecast creation, the assistant's tools, recommendation generation, the performance report.
