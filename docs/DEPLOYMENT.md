# Deployment

**Status: UNVERIFIED.** Docker and a test database were not available where this was written. Every container, Compose and
database step below is written carefully but has **not been run**. Each step is marked. Do not call a deployment
"production ready" until the checklist at the end is complete. Data is **MOCK**; no Amazon Ads API connection exists;
`AD_EXECUTION_ENABLED` is `false` and the API refuses to start otherwise.

## What a deployment consists of
| Part | Image / source | Notes |
|---|---|---|
| `db` | `pgvector/pgvector:pg16` | pgvector is required (migration `0005`). Internal network only, no published port. |
| `migrate` | backend image | One-shot `alembic upgrade head` as the bootstrap role (creating the `vector` extension needs a superuser). |
| `roles` | `pgvector/pgvector:pg16` | One-shot: gives the `adpilot_app` role a login password. |
| `backend` | `backend/Dockerfile` | FastAPI, runs as the limited `adpilot_app` role, read-only filesystem. |
| `frontend` | `frontend/Dockerfile` | Unprivileged nginx on 8080, proxies `/api/` to the backend. |
| TLS terminator | yours | A reverse proxy or load balancer in front of port 8080 (HSTS, certificates). Not provided. |
| Identity provider | yours | Any OIDC provider; supplies the JWT issuer, audience and JWKS URL (ADR 0021). **Not chosen yet.** |

## Prerequisites
- Docker Engine with Compose v2.24+ (`env_file` with `required: false`). *(unverified)*
- An OIDC provider with an API audience, and your users created in the `users`/`memberships` tables with
  `auth_subject` set (**there is no sign-up flow yet**).
- The frontend has **no login flow**: it still sends the development header, which a production API ignores. A deployed
  frontend cannot talk to an authenticated API until OIDC login is built.

## Procedure
1. **Configure.** `cp .env.production.example .env.production`; fill every value with long random secrets. Restrict the file
   (`chmod 600`). Never commit it (it is gitignored). *(unverified)*
2. **Validate the Compose file.** `docker compose --env-file .env.production -f docker-compose.prod.yml config -q`. The YAML
   parses (checked with PyYAML); Compose semantics were not checked. *(partly verified)*
3. **Build.** `docker compose --env-file .env.production -f docker-compose.prod.yml build`. Backend dependencies install from
   `backend/requirements.lock` with `--require-hashes`. *(unverified; CI job `images` builds both images)*
4. **Start.** `docker compose --env-file .env.production -f docker-compose.prod.yml up -d`. Order: db healthy -> `migrate`
   completes -> `roles` completes -> backend healthy -> frontend. *(unverified)*
5. **Check.** `curl -fsS http://127.0.0.1:8080/api/health` returns `{"status":"ok", "execution_enabled": false, ...}`;
   `.../api/ready` returns `{"status":"ready"}` (database reachable). A request without a token to any `/api/v1/` route
   returns 401. `/api/docs` and `/api/openapi.json` return 404. *(unverified)*
6. **Seed demo MOCK data only if wanted:**
   `docker compose -f docker-compose.prod.yml run --rm backend python -m app.cli seed-mock` (about 3 minutes). It must be run
   against a database that holds no real data. *(unverified)*

## Database roles (why the backend is not the owner)
Row-level security isolates tenants, but a superuser or `BYPASSRLS` role ignores every policy. The development Compose file
connects as the bootstrap superuser and relies on `SET LOCAL ROLE adpilot_app` per request: one forgotten `SET ROLE` would
expose every tenant. The production stack therefore connects **as** `adpilot_app` (`DB_APP_ROLE` unset), and on startup the API
checks the role its queries actually run as and **refuses to serve** if it is a superuser or has `BYPASSRLS`
(`assert_rls_enforced`; unit-tested with a fake session, database test unverified).

## Environment variables (production)
| Variable | Purpose |
|---|---|
| `POSTGRES_PASSWORD` | bootstrap superuser, used only by `db`, `migrate`, `roles` |
| `APP_DB_PASSWORD` | password of the `adpilot_app` login role |
| `AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `AUTH_JWKS_URL` | all three, https JWKS URL; otherwise the API will not start |
| `CORS_ORIGINS` | JSON list of allowed browser origins; usually `[]` behind the proxy |
| `LLM_PROVIDER` and `LLM_*`, `OPENAI_API_KEY`, `EMBEDDING_*` | optional assistant; off by default; spend caps are required when on |
| `AD_EXECUTION_ENABLED` | fixed to `"false"` in the compose file |

Passwords are placed inside connection URLs: use characters that need no URL-encoding, or percent-encode them.

## Secrets
Secrets are injected through `.env.production` and never baked into images (`.dockerignore` excludes `.env`; CI runs gitleaks).
This is the minimum. The target is a managed secret store (Docker/Swarm secrets, Vault, or your cloud's) with rotation; the
application reads settings from the environment, so that is a deployment change, not a code change. Rotate `APP_DB_PASSWORD`
with `ALTER ROLE adpilot_app PASSWORD ...` and a backend restart.

## Reproducibility
- Python dependencies: `backend/requirements.lock`, universal (Linux and Windows), hash-pinned. Regenerate with
  `uv pip compile pyproject.toml --universal --python-version 3.12 --generate-hashes -o requirements.lock` and commit the diff.
  Dev tools (pytest, ruff, mypy) are **not** locked.
- Base images are pinned by tag, **not by digest**. Node is `node:26-alpine` to match `.nvmrc`; confirm its support status
  before relying on it. GitHub Actions are pinned by major version, not commit SHA.

## Rollback
Redeploy the previous image tag. Migrations: `alembic downgrade -1` is possible for `0006` (drops the recommendation tables:
**data loss**) and earlier migrations; take a backup first (see OPERATIONS.md). Never downgrade a database that holds real
approval history without exporting it.

## Production-readiness checklist (all open unless stated)
- [ ] Both images build (`docker build`), and containers start healthy under Compose.
- [ ] `alembic upgrade head` succeeds on an empty database, and the 76+ database-backed tests pass (`TEST_DATABASE_URL`).
- [ ] Startup refuses to serve as a superuser role (database test `test_role_check.py`).
- [ ] An OIDC provider is chosen, users are provisioned, and the frontend has a login flow.
- [ ] TLS termination and HSTS in front of the frontend.
- [ ] Backup taken and a **restore rehearsed** (OPERATIONS.md).
- [ ] Monitoring and alerting wired to the logs/health endpoints (OPERATIONS.md).
- [ ] Load/performance check (analytics query timing on the 646k-row seed has never been run).
- [x] Dependency vulnerability scan clean (pip-audit on the lock, `npm audit --omit=dev`): run 2026-11-12, no findings.
- [x] Execution cannot be enabled: `AD_EXECUTION_ENABLED=true` is refused at startup; no provider implementation exists.
