# ADR 0024: Deployment hardening (M8)

Status: Accepted (implemented 2026-11-12, pending owner review; container and database behavior UNVERIFIED)

## Context
M8 asks for a reproducible, secure deployment procedure. Docker and a test database were not available when this was written,
so nothing here could be built or run. The decisions below are made so that CI (which has both) can verify them on the first
push, and so the documentation states exactly what is and is not proven.

## Decision
- **Reproducible backend image:** dependencies install from a universal, hash-pinned `requirements.lock` (`uv pip compile
  --universal --generate-hashes`), with `--require-hashes`. The app runs from source (`PYTHONPATH=/app`) instead of being
  `pip install`ed, which also puts `knowledge/` and `migrations/` where the CLI and Alembic look (the previous image installed the
  package into site-packages and never copied `knowledge/`). Multi-stage, non-root, `HEALTHCHECK`, `--no-server-header`.
- **Hardened frontend image:** unprivileged nginx on 8080 with security headers (CSP, nosniff, frame deny, referrer,
  permissions, COOP), gzip, cache rules, `server_tokens off`, upstream timeouts. TLS/HSTS belongs to the proxy in front.
- **Production Compose** (`docker-compose.prod.yml`): one-shot `migrate` and `roles` jobs, backend connects as the limited
  `adpilot_app` role, database on an internal network without published ports, dropped capabilities, `no-new-privileges`,
  read-only root filesystems, memory limits, restart policies, bounded logs. Development Compose stays as it was, with a health
  condition and the new frontend port.
- **Refuse unsafe runtime states in production:** the API description (`/docs`, `/redoc`, `/openapi.json`) is not served, and
  startup fails if the effective database role is a superuser or has `BYPASSRLS` (`assert_rls_enforced`).
- **CI:** coverage report (not gated), `pip-audit` on the lock and `npm audit --omit=dev`, `docker build` of both images, a
  start-and-`/health` smoke test of the backend image, and `docker compose config` of both Compose files.

## Consequences
- **Unverified until CI or a Docker host runs it:** both Dockerfiles, both Compose files, nginx config (CSP in particular may
  need tuning in a browser; styles allow inline for the charts), the `roles` job's psql quoting, and the startup role check against
  a real database.
- Known remaining gaps, listed in `docs/DEPLOYMENT.md`: no OIDC provider or frontend login, no TLS terminator, no monitoring or log
  shipping, no backup rehearsal, no performance measurement, base images and Actions not pinned by digest/SHA, dev dependencies
  not locked, no migration-at-scale story beyond "run the job once", per-process rate limit and JWKS cache.
- The development Compose file still connects as a superuser and relies on `SET ROLE`; it is for local use only.
- Dependency scan on 2026-11-12: no known vulnerabilities in the locked runtime packages or production npm packages. That is a
  point-in-time result.
