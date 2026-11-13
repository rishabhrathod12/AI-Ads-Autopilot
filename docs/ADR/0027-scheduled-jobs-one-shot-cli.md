# ADR 0027: Scheduled jobs as a one-shot CLI triggered by cron

Status: Accepted by the owner on 2026-11-13 (option "one-shot CLI + cron"). Implemented and tested against PostgreSQL 18;
**not yet run from a container or a real scheduler**.

## Context
Forecasts and recommendation expiry only happened when a person triggered them. ADR 0009 said to start with an in-process worker
or a Postgres-backed queue and revisit on measured need. Two small, idempotent jobs do not justify a queue, and an in-process
timer would run once per API worker (duplicate work, races).

## Decision
- `python -m app.cli run-jobs [--jobs expire forecast] [--org-id UUID ...] [--force]` runs once and exits (code 1 if any job
  failed). cron, systemd or a Compose scheduler triggers it. The API process runs no timers.
- **Jobs** (`app/application/jobs/maintenance.py`, pure use cases over ports):
  - `expire`: pending recommendations past `expires_at` become `expired`, one audit entry each (`recommendation.expired`, actor
    `system:jobs`). Approved or rejected recommendations are never touched.
  - `forecast`: store a fresh 14-day account forecast (default seasonal-naive, ADR 0013) unless one younger than 20 hours exists
    (`--force` overrides). No data, too little history or no traffic is `skipped`, not `failed`. Audited as `forecast.created`.
- **Runner** (`app/infrastructure/db/job_runner.py`): lists organizations, then runs each (organization, job) pair in its **own
  ordinary tenant session and transaction** (RLS applies exactly as for a request). A per-pair PostgreSQL advisory lock makes an
  overlapping run skip instead of duplicating work. A failure is logged without its message and reported; other pairs still run.
- **Listing organizations needs one deliberate exception to RLS** (migration 0008): a read-only policy on `organizations` that
  admits rows while the transaction-local setting `app.maintenance` is `on`. Only `maintenance_session` sets it, in a transaction
  that reads organization ids and nothing else (tested: recommendations, audit logs, memberships and performance data stay
  invisible in it). Without the setting the policy matches nothing. A SECURITY DEFINER function was rejected: FORCE RLS applies
  to the function owner unless it has BYPASSRLS, so it would return nothing on a managed database.
- The jobs connect as the same limited application role as the API. They never call an advertising provider and cannot execute
  any change.

## Operating it
Run daily. Example (Compose, **unverified: no Docker here**):
`docker compose -f docker-compose.prod.yml run --rm --no-deps backend python -m app.cli run-jobs`. cron line:
`15 3 * * * cd /srv/adpilot && docker compose -f docker-compose.prod.yml run --rm --no-deps backend python -m app.cli run-jobs`.
Output is one line per (job, organization); the exit code is the alert signal.

## Consequences
- Expiry still happens lazily when someone decides on an overdue recommendation (ADR 0022); the job just makes the stored status
  and the audit trail catch up.
- The `app.maintenance` setting is trusted the way `app.org_id` is: a SQL-injection-level compromise could set it. It exposes only
  organization ids, names and plans.
- Not done: syncing (there is no real data source; the mock load stays a manual CLI step), alerting on a failed run, a jobs
  history table, per-organization schedules, per-campaign forecasts, running jobs for a single tenant from the UI.
