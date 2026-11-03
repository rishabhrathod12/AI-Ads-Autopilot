# AdPilot AI

AI-powered Amazon PPC (Sponsored Products) analysis and optimization platform.
Agentic AI + Generative AI + Machine Learning, built as a portfolio project with a path to a multi-tenant SaaS.

> **Status: M4 (grounded single assistant with RAG) implemented; M3 accuracy checks that failed are documented.**
> No multi-agent graph (M5), approval workflow or Amazon connectivity yet.
> **Data: MOCK only.** No Amazon Ads API connection exists or is simulated. Synthetic data is always labeled `MOCK`.
> **Safety: `AD_EXECUTION_ENABLED=false`.** No advertising changes are ever made without explicit human approval.

## What it will do
Dashboard KPIs, campaign and keyword intelligence, wasted-spend and negative-keyword detection, bid and budget recommendations, ML forecasting, a grounded AI PPC assistant, and a human approval center with audit logs.

## Core principles
1. Deterministic Python for all calculations; LLMs only reason, plan and explain.
2. Provider-independent: business logic depends on `AdvertisingDataProvider`, never on Mock or Amazon implementations.
3. Every record carries source and timestamp. Mock data is never presented as live.
4. Human approval required for every action; execution disabled by default and fails safe.
5. Tenant isolation from the first migration.

## Documentation map
| Doc | Purpose |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Product requirements |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture |
| [docs/AGENT_ARCHITECTURE.md](docs/AGENT_ARCHITECTURE.md) | LangGraph agents and tools |
| [docs/DATABASE_DESIGN.md](docs/DATABASE_DESIGN.md) | Schema, tenancy, provenance |
| [docs/API_DESIGN.md](docs/API_DESIGN.md) | REST API conventions and endpoints |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model and safeguards |
| [docs/MOCK_DATA.md](docs/MOCK_DATA.md) | Synthetic data model and planted scenarios |
| [docs/ANALYTICS.md](docs/ANALYTICS.md) | KPI rules, findings, anomalies, forecasting |
| [docs/ML_EVALUATION.md](docs/ML_EVALUATION.md) | Held-out results, including failures |
| [docs/ASSISTANT.md](docs/ASSISTANT.md) | Grounded assistant, validator, golden and held-out evaluations |
| [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) | Milestones M1-M9 |
| [docs/ADR/](docs/ADR/) | Architecture decision records |

## Planned repository layout
```
backend/    FastAPI app (domain / application / infrastructure / api)
frontend/   React 19 + TypeScript + Vite
docs/       Documentation and ADRs
```

## Quickstart (M1)
Prerequisites: Python 3.12, Node 26 (see `.nvmrc`), PostgreSQL 16 (or Docker Desktop).

**Docker (recommended)**
```
cp .env.example .env        # set POSTGRES_PASSWORD
docker compose up --build   # frontend http://localhost:8080, API http://localhost:8000/health
```

**Without Docker**
```
cd backend
python -m venv .venv && .venv/Scripts/activate      # Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/adpilot
alembic upgrade head
uvicorn app.api.asgi:app --reload

cd ../frontend && npm install && npm run dev        # http://localhost:5173
```

**Load demo MOCK data** (after `alembic upgrade head`; ~3 min for 120 days on a Windows dev machine)
```
cd backend && python -m app.cli seed-mock
```

**Quality checks**
```
cd backend && ruff check . && ruff format --check . && mypy && lint-imports
TEST_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5432/adpilot_test pytest   # DB tests skip if unset
cd frontend && npm run lint && npm run typecheck && npm test && npm run build
```
The RLS tests recreate the schema (`alembic downgrade base` then `upgrade head`): use a dedicated test database.

## Running the analytics API locally (M3)
```
cd backend && .venv/Scripts/uvicorn app.api.asgi:app --port 8010       # use a port not taken by other projects
python -m app.cli seed-mock                                          # ~3 min, needs DATABASE_URL + alembic upgrade head
curl -H "X-Dev-Org-Id: <org uuid printed by seed-mock>" http://127.0.0.1:8010/api/v1/dashboard/summary
```
The tenant header is **development only**; production refuses to start until real authentication exists.
Slow accuracy evaluations are excluded from the default test run: `pytest -m eval tests/evals -s`.

## Workflow
Milestone-gated. Each milestone needs explicit owner approval before the next begins.
