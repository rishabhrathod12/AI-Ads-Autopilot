# Operations

**Status: UNVERIFIED.** None of the commands below has been run against a deployed stack. No recovery time or recovery
point objective has been agreed; the targets here are proposals.

## Health and readiness
- `GET /health`: process is up (no database needed). Used by the container HEALTHCHECK.
- `GET /ready`: database reachable (`SELECT 1`). Use it for load-balancer readiness, not for liveness.
- The response also reports `data_source` (`MOCK`) and `execution_enabled` (must be `false`).

## Logs
JSON, one object per line, to stdout (Docker keeps 5 x 10 MB per container). Fields on every request line (`event: request`):
`method`, `route` (template, not raw path), `status`, `duration_ms`, `request_id`, `org_id`, `role`. Errors:
`event: unhandled_exception` with `request_id`, `error_type`, a scrubbed `error_message` and `frames`. Query strings, headers,
bodies and tokens are never logged; secrets and email addresses in values are redacted (pattern-based, see ADR 0023).

Useful filters (with `jq`): `select(.event=="request" and .status>=500)`; `select(.event=="unhandled_exception")`;
slow requests: `select(.event=="request" and .duration_ms>5000)`. Correlate with a client's `X-Request-ID` header, echoed on every
response. Logs are **not shipped anywhere**: add a collector (Loki, CloudWatch, ELK) before relying on them.

## Monitoring and alerts
**Built (2026-11-13):** `GET /metrics` on the backend, Prometheus text format, no extra dependency: `adpilot_http_requests_total{method,route,status}`
and `adpilot_http_request_duration_seconds` (histogram by route). Labels are bounded (route template only, unmatched paths are one label, odd
methods are `OTHER`; never an organization, user or id). Counts are **per process** (one backend instance today). nginx forwards only `/api/`,
so `/metrics` is not reachable from outside; scrape it on the internal network. **Not built:** tracing, a Prometheus/Alertmanager setup, dashboards,
and job metrics (the `run-jobs` exit code is the only job signal).

Proposed alerts, to be wired by whoever runs the monitoring (none exists yet):
- `/ready` failing for 2 minutes -> page.
- 5xx rate above 1% over 5 minutes -> page: `sum(rate(adpilot_http_requests_total{status=~"5.."}[5m])) / sum(rate(adpilot_http_requests_total[5m]))`;
  any `unhandled_exception` log line -> ticket.
- p95 above 5 s on analytics routes (forecast creation and the assistant legitimately take several seconds; the keyword routes already take 1-3 s
  under light load, see the load test below): `histogram_quantile(0.95, sum by (le, route) (rate(adpilot_http_request_duration_seconds_bucket[5m])))`.
- A rising rate of 401/403: `sum(rate(adpilot_http_requests_total{status=~"401|403"}[5m]))` (per-source review needs the access log; there is no failed-login rate limit yet).
- Disk use of the `pgdata` volume above 80%.
- LLM spend: the app enforces daily and monthly caps (`llm_usage` table); alert before the cap, not at it.

## Backups and restore
Postgres holds everything, including approvals and the audit trail (insert-only tables must survive).
- **Logical backup (daily, proposed):**
  `docker compose exec -T db pg_dump -U adpilot -Fc adpilot > adpilot-$(date +%F).dump`
  Encrypt it and store it off the host. Keep 7 daily and 4 weekly copies (proposal).
- **Point-in-time recovery:** enable WAL archiving on the database (`archive_mode`, `archive_command`) or use a managed Postgres
  that provides it. A daily dump alone means up to 24 hours of lost approvals/audit entries.
- **Restore (rehearse quarterly):**
  1. start an empty database: `docker compose up -d db`;
  2. **create the application role first** (roles are cluster-wide and are not in a database dump, and `alembic upgrade head` does not
     create it when the dump is current):
     `docker compose exec -T db psql -U adpilot -d adpilot -c "CREATE ROLE adpilot_app NOLOGIN NOSUPERUSER NOBYPASSRLS"`
     (skip if it already exists). Without it every `GRANT` in the restore fails and the tables come back with no privileges for the app;
  3. `docker compose exec -T db pg_restore -U adpilot -d adpilot --clean --if-exists --no-owner < adpilot-YYYY-MM-DD.dump`
     and expect **no errors**;
  4. run `alembic upgrade head` (no-op if the dump is current) and the `roles` job (it sets the role's login password; passwords are not
     in the dump);
  5. verify: `/ready`, row counts of every table against the source, the number of RLS policies, FORCE-RLS tables and grants to
     `adpilot_app` against the source, and a sign-in with a test user.
- **Rehearsed once, 2026-11-13, partly.** A logical dump (`pg_dump -Fc`, 3.0 MB, 23 tables, 484,433 metric rows) of a seeded PostgreSQL
  18.6 database was restored into a **fresh, empty PostgreSQL 18.6 cluster**. The procedure as first written failed (all 53 `GRANT`s
  errored because the role did not exist; this is why step 2 was added). With step 2: 0 errors, all 23 table row counts equal, 24/24
  policies, 53/53 grants, 19/19 FORCE-RLS tables, migration version 0008, `run-jobs` ran under RLS as `adpilot_app`, and a session as
  that role with no tenant saw 0 rows. **Not rehearsed:** the Compose `db` container and PostgreSQL 16, the `roles` job, an encrypted
  off-host copy, point-in-time recovery, a larger volume, and the time it takes (the 3 MB dump restored in seconds).

## Load test (2026-11-13, one run, developer laptop)
**Setup:** the real API (one uvicorn worker, development mode) against PostgreSQL 18.6 on the same machine, the 484,433-row mock seed (90
days, 12 campaigns, 1,674 targets, 4,345 search terms), a closed loop of N concurrent clients each sending sequential GETs. Not a production
shape: no TLS, no nginx, client and database on the same CPU, a single account, dev-header auth.

| Route | 1 client p50 | 5 clients p50 / p95 | 20 clients p50 / p95 | Throughput at 20 |
|---|---|---|---|---|
| dashboard summary | 53 ms | 138 / 555 ms | 754 / 1,144 ms | 26 req/s |
| campaigns | 53 ms | 325 / 738 ms | 1.4 s / 2.3 s | 13 req/s |
| anomalies | 79 ms | 446 / 834 ms | 1.8 s / 2.6 s | 9 req/s |
| top keywords | 753 ms | 2.3 / 3.8 s | 7.4 / 9.3 s | 2.4 req/s |
| keyword findings | 1.5 s | 3.6 / 6.7 s | **19 / 28 s** | **1.0 req/s** |
| mixed, 20 clients, all routes | | | p50 4.9 s, max 11.7 s | 2.7 req/s |

- **Zero errors** (no 5xx, no timeouts, no connection failures) at every level.
- **It saturates, it does not fail.** Throughput stops rising after 1-5 clients, so latency grows linearly with the queue. The two slow routes
  are bound by the database, not by Python: one aggregate over `performance_daily` for search terms is a parallel sequential scan of all rows,
  about 390 ms (`EXPLAIN ANALYZE`), and findings runs several. A covering index on `(organization_id, entity_type, date)` was tried on a
  scratch copy: **only 19% faster (314 ms) for 51 MB**, so it is not recommended.
- **What would actually help (not done, needs a decision):** a short-lived cache of analytics results keyed by organization, route,
  parameters and the latest data date (data only changes on a sync), or pre-aggregated period totals. Both trade freshness or storage for
  speed. Until then, a handful of simultaneous users on the keyword routes will wait seconds.
- **Not measured:** other hardware, many organizations at once, the assistant and forecast creation under load, a separate database server,
  PostgreSQL 16, long runs (memory growth), and the connection-pool limit (default 5 plus 10 overflow per process).

## Routine tasks
| Task | How |
|---|---|
| Apply a release | build new images, run `migrate`, then restart `backend` and `frontend` (single instance: brief downtime) |
| Rotate DB password | `ALTER ROLE adpilot_app PASSWORD '...'`, update `.env.production`, restart `backend` |
| Rotate JWT keys | handled by the identity provider (JWKS is cached 10 minutes by the API) |
| Expire stale recommendations, refresh forecasts | `python -m app.cli run-jobs` once a day from cron (ADR 0027; Compose: `docker compose -f docker-compose.prod.yml run --rm --no-deps backend python -m app.cli run-jobs`, **unverified**). A pending recommendation past `expires_at` is already refused for approval without it |
| Investigate an approval | `GET /api/v1/audit-logs?entity_type=recommendation&entity_id=<id>` (admin) |

## Known operational limits
- Single backend instance only: migrations are a separate job, but the assistant rate limit and the JWKS cache are per process.
- The jobs only run when cron (or similar) starts them, and nothing alerts on a failed run except its exit code; no log shipping, no alerting set up (request metrics exist), no failed-login throttling.
- Analytics query performance on a realistic data volume has not been measured.
