# CLAUDE.md

AdPilot AI: Amazon PPC analytics and optimization platform. Backend FastAPI/SQLAlchemy/Postgres+pgvector (`backend/`),
React 19 + Vite (`frontend/`), docs and ADRs in `docs/`. Status: `docs/PROJECT_STATUS.txt`.

## Non-negotiable rules
- Data is MOCK only; label it `MOCK`. Do not claim Amazon Ads API integration exists.
- `AD_EXECUTION_ENABLED` stays false; no ad change without explicit human approval.
- Business calculations are deterministic Python; LLMs only reason and explain.
- Strict tenant isolation (RLS); keep audit trails; never expose secrets (`backend/.env` is gitignored, never print it).
- Never weaken tests, thresholds or evaluation criteria to get a pass. Failed evals are recorded as known failures.
- Milestone-gated: wait for owner approval before starting the next milestone; major architecture changes need approval.

## Commands (backend, from `backend/`)
- `.venv/Scripts/ruff check . && .venv/Scripts/ruff format --check . && .venv/Scripts/mypy && .venv/Scripts/lint-imports`
- `.venv/Scripts/pytest -q` (DB tests skip unless `TEST_DATABASE_URL` is set; RLS tests recreate the schema, use a dedicated DB)
- `.venv/Scripts/pytest -m eval tests/evals -s` (slow accuracy evals)
- Frontend (from `frontend/`): `npm run lint && npm run typecheck && npm test && npm run build`
