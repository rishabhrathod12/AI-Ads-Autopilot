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

## Monitoring and alerts (to build)
No metrics endpoint, tracing or alerting exists. Proposed minimum, from the logs and health endpoints:
- `/ready` failing for 2 minutes -> page.
- 5xx rate above 1% over 5 minutes -> page; any `unhandled_exception` -> ticket.
- p95 `duration_ms` above 5 s on analytics routes (forecast creation and the assistant legitimately take several seconds).
- Repeated 401/403 from one source -> review (there is no failed-login rate limit yet).
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
  2. `docker compose exec -T db pg_restore -U adpilot -d adpilot --clean --if-exists --no-owner < adpilot-YYYY-MM-DD.dump`;
  3. run `alembic upgrade head` (no-op if the dump is current) and the `roles` job (role passwords are not in the dump);
  4. verify: `/ready`, row counts of `recommendations`, `approval_records`, `audit_logs`, and a login with a test user.
- **Not rehearsed.** A backup that has never been restored is not a backup.

## Routine tasks
| Task | How |
|---|---|
| Apply a release | build new images, run `migrate`, then restart `backend` and `frontend` (single instance: brief downtime) |
| Rotate DB password | `ALTER ROLE adpilot_app PASSWORD '...'`, update `.env.production`, restart `backend` |
| Rotate JWT keys | handled by the identity provider (JWKS is cached 10 minutes by the API) |
| Expire stale recommendations | none runs automatically; a pending recommendation past `expires_at` is treated as expired and can no longer be approved |
| Investigate an approval | `GET /api/v1/audit-logs?entity_type=recommendation&entity_id=<id>` (admin) |

## Known operational limits
- Single backend instance only: migrations are a separate job, but the assistant rate limit and the JWKS cache are per process.
- No automatic expiry job, no alerting, no log shipping, no metrics, no failed-login throttling.
- Analytics query performance on a realistic data volume has not been measured.
