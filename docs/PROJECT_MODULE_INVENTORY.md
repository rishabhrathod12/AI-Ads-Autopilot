# Project Module Inventory

Audit date: 2026-11-13. Method: read-only inspection of the source tree at commit `b321aff` (working tree clean).
Every count below was taken from the code, not from earlier documentation. Where a number came from a document or a
stored result instead (not re-run in this audit), it is marked **[doc]**.

Evidence levels used throughout this audit set:

| Level | Meaning |
|---|---|
| **Implemented** | The code exists in the repository. |
| **Tested** | Automated tests exercise it (unit and/or Postgres-backed). |
| **Integrated** | It is reachable from a running application path (API, UI, CLI), not only from tests. |
| **Production-ready** | Verified in the target environment under real data and real dependencies. **Nothing in this project reaches this level**; data is MOCK only and the container images and Compose stack have never been built or run. |

## 1. Headline counts

| Item | Count | Evidence |
|---|---|---|
| Non-empty backend Python modules (`backend/app`) | **103** (12,670 lines) | counted from `backend/app/**/*.py`, excluding empty `__init__.py` |
| Backend architectural layers | 3 (`domain`, `application`, `infrastructure`) plus `api` | enforced by import-linter contracts: "Layered architecture KEPT", "Domain is framework-free KEPT" |
| Port interfaces (`Protocol` classes) | 15 | `backend/app/application/ports/*.py` (12 files) |
| Alembic migrations | 6 (`0001`..`0006`) | `backend/migrations/versions/` |
| Frontend production modules | 12 TypeScript/React files (+ `styles.css`) | `frontend/src/` |
| Database tables | **22** | section 4 |
| HTTP endpoints | **25** | section 5 |
| Forecasting models | **7** (4 baselines, 3 fitted) | `backend/app/application/forecasting/models.py` |
| Change-detection algorithms | **2** (CUSUM variants) | `backend/app/domain/anomaly.py` |
| Statistical inference procedures in use | **2** (exact binomial tail, Clopper-Pearson bound) | `backend/app/domain/stats.py` |
| Trained or persisted model artifacts | **0** | every model is fitted per request on the series it is given; no pickles, registry or training job exists |
| LLM provider integrations | **1** (OpenAI Chat Completions) + 1 offline test double | `backend/app/infrastructure/llm/` |
| Embedding provider integrations | **1** (OpenAI Embeddings) + 1 offline hashing stand-in | same folder |
| LLM prompts | **6** (assistant system, supervisor, specialist template, synthesizer, plus two "nudge" messages) | section 8 |
| RAG components | 8 | section 7 |
| Agents (LangGraph roles) | 1 supervisor, 4 specialists, 1 synthesizer, 1 validator (code) | `backend/app/application/agents/` |
| LangGraph graphs / nodes | **1 graph, 4 nodes** (`plan`, `specialist`, `synthesize`, `validate`) | `backend/app/application/agents/graph.py:build` |
| Tests collected by default | **840** backend + **60** frontend | `pytest --collect-only`, `vitest run` |
| Eval tests (excluded by default) | **48** | `pytest -m eval --collect-only` |

Test-count detail:

| Suite | Test functions | Notes |
|---|---|---|
| `backend/tests/unit` | 537 | no database needed |
| `backend/tests/integration` | 63 | need `TEST_DATABASE_URL`; skipped otherwise (`tests/integration/conftest.py`) |
| `backend/tests/contract` | 6 | provider contract |
| `backend/tests/evals` | 49 | marked `eval`, deselected by default |
| `frontend/src/**/*.test.ts(x)` | 60 | App 7, ApprovalCenter 12, Assistant 6, Dashboard 12, format 7, LineChart 9, KpiTiles 7 |

I re-ran, in this session, before this audit: ruff, ruff format, mypy (strict), import-linter, the backend suite
(**764 passed, 76 skipped, 48 deselected**: the skips are the Postgres tests, because `TEST_DATABASE_URL` was not set),
frontend lint, typecheck and 60 tests (all pass). The 840-pass/0-skip figure with a database is from the project's own
verification record **[doc: `docs/PROJECT_STATUS.txt`, 2026-11-12]**; I did not re-run it.

## 2. Functional sections

| # | Section | Where | State |
|---|---|---|---|
| 1 | Domain model and KPI engine | `backend/app/domain/` | Implemented, tested, integrated |
| 2 | Mock advertising data and provider port | `backend/app/infrastructure/providers/` | Implemented, tested, integrated (CLI seed only) |
| 3 | Sync (ingestion) service | `backend/app/application/sync_service.py` | Implemented, tested, integrated via CLI only |
| 4 | Persistence, RLS, migrations | `backend/app/infrastructure/db/`, `backend/migrations/` | Implemented, tested (with DB), integrated |
| 5 | Analytics (dashboard, campaigns, keywords, anomalies) | `backend/app/application/analytics/` | Implemented, tested, integrated |
| 6 | Forecasting | `backend/app/application/forecasting/` | Implemented, tested, integrated (manual POST) |
| 7 | Deterministic reporting | `backend/app/application/reporting/` | Implemented, tested, integrated (API only) |
| 8 | LLM stack (port, retry, budget, redaction) | `backend/app/application/llm/`, `infrastructure/llm/` | Implemented, tested, integrated; run live only in evals |
| 9 | Assistant (tools, evidence, faithfulness validator) | `backend/app/application/assistant/` | Implemented, tested, integrated |
| 10 | Knowledge base / RAG | `backend/app/application/knowledge/`, `backend/knowledge/` | Implemented, tested, integrated (needs ingestion + embedding config) |
| 11 | Agent graph | `backend/app/application/agents/` | Implemented, tested; integrated only when `ASSISTANT_ENGINE=graph` (default is `single`) |
| 12 | Recommendations and approval | `backend/app/application/optimization/`, `recommendations/`, `domain/recommendations.py` | Implemented, tested, integrated |
| 13 | Authentication and roles | `backend/app/api/tenancy.py`, `infrastructure/auth/` | Implemented, tested; no identity provider chosen; frontend has no login |
| 14 | Audit and observability hygiene | `backend/app/application/audit.py`, `logging.py`, `infrastructure/db/audit_store.py` | Implemented, tested |
| 15 | Amazon Ads connection layer | `backend/app/application/ads_api/`, `infrastructure/ads_api/`, `infrastructure/providers/amazon/` | Implemented, tested in isolation; **not wired to anything**; no endpoints |
| 16 | Startup safety checks | `backend/app/startup_checks.py` | Implemented, tested, integrated |
| 17 | Frontend | `frontend/src/` | Implemented, tested, integrated |
| 18 | Deployment files and CI | `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`, `docker-compose*.yml`, `.github/workflows/ci.yml` | Written; **never built or run** |

## 3. Backend module list

### 3.1 `app/domain` (pure rules, no framework imports) - 11 modules
| File | Purpose |
|---|---|
| `domain/ads/models.py`, `ids.py` | Campaign, AdGroup, Target, SearchTerm, DailyMetrics, DataSource, ChangeRequest |
| `domain/kpi/metrics.py` | `acos_pct`, `roas`, `ctr_pct`, `cpc_paise`, `cvr_pct`, `aov_paise`, `MetricTotals`, `Kpis` (Decimal and integer paise) |
| `domain/kpi/periods.py` | `DateRange`, maturity inference, period aggregation and comparison |
| `domain/kpi/explain.py` | ACOS change decomposition into CPC, conversion rate and order value |
| `domain/anomaly.py` | Bernoulli and Gaussian CUSUM traces, run finding, MAD scale |
| `domain/stats.py` | Binomial CDF/tail, Clopper-Pearson lower bound |
| `domain/recommendations.py` | Recommendation, guardrails, content hash, lifecycle, ApprovalRecord, ExecutionAttempt |
| `domain/auth.py`, `tenant.py`, `audit.py` | Role, Principal, TenantContext, AuditEntry |

### 3.2 `app/application` (use cases and ports) - 50 modules
| Folder | Modules | Purpose |
|---|---|---|
| `analytics/` | `dashboard`, `campaigns`, `keywords`, `keyword_tables`, `anomalies`, `thresholds` | Read-side analytics over a `PerformanceReader` |
| `forecasting/` | `models`, `metrics`, `backtest`, `service`, `reports`, `records` | Forecasters, rolling backtest, selection, storage |
| `reporting/` | `report` | Deterministic JSON/Markdown/CSV performance report |
| `optimization/` | `generate` | Bid, budget and negative-keyword calculators |
| `recommendations/` | `service` | Approval workflow and blocked execution gate |
| `assistant/` | `loop`, `tools`, `evidence`, `faithfulness`, `prompts`, `service`, `records` | Single-assistant loop and the claim validator |
| `agents/` | `graph`, `specialists`, `prompts` | Supervisor/specialist/synthesizer/validator graph |
| `knowledge/` | `chunking`, `ingest` | Markdown chunking and idempotent ingestion |
| `llm/` | `budget`, `cost`, `embedding`, `redact`, `resilient` | Spend caps, retries, redaction |
| `ads_api/` | `errors`, `retry`, `token_manager` | Amazon connection primitives |
| `ports/` | 12 files | Interfaces: ads provider/store, assistant store, audit store, auth, credential store, embedding, forecast store, knowledge store, LLM, performance reader, recommendation store |
| top level | `sync_service`, `audit`, `jsonable` | Ingestion, audit-entry builder, JSON helper |

### 3.3 `app/infrastructure` - 29 modules
`db/` (14: 7 model files, 7 stores/readers/session), `llm/` (5), `providers/` (4: factory, mock generator, mock provider, amazon provider), `ads_api/` (4: http_client, lwa, token_cipher, vault), `auth/` (2: jwt_verifier, membership).

### 3.4 `app/api` - 9 modules
`main.py` (app factory, middleware), `tenancy.py` (principal resolution, role guards, tenant scope), `errors.py`, `ratelimit.py`, `serialize.py`, `asgi.py`, `routers/analytics.py`, `routers/assistant.py`, `routers/recommendations.py`.

### 3.5 Top level - 4 modules
`config.py`, `logging.py`, `startup_checks.py`, `cli.py`.

## 4. Database: 22 tables

| Migration | Tables |
|---|---|
| `0001_foundation` | `organizations`, `users`, `memberships`, `audit_logs` |
| `0002_advertising_data` | `seller_accounts`, `advertising_profiles`, `campaigns`, `ad_groups`, `keywords_targets`, `search_terms`, `report_runs`, `performance_daily` |
| `0003_forecast_runs` | `forecast_runs` |
| `0004_assistant` | `assistant_threads`, `assistant_runs`, `assistant_messages`, `llm_usage` |
| `0005_knowledge` | `knowledge_documents`, `knowledge_chunks` (pgvector `vector(1536)`) |
| `0006_recommendations` | `recommendations`, `approval_records`, `execution_attempts` |

Details, relationships and isolation: see `COMPLETE_PROJECT_ARCHITECTURE.md` section 7.

## 5. API endpoints: 25

| Router file | Count | Endpoints |
|---|---|---|
| `api/main.py` | 2 | `GET /health`, `GET /ready` |
| `api/routers/analytics.py` | 11 | `GET /api/v1/dashboard/summary`, `/dashboard/trends`, `/campaigns`, `/campaigns/changes`, `/keywords`, `/search-terms`, `/keywords/findings`, `/anomalies`, `/forecasts/account`, `/reports/performance`; `POST /api/v1/forecasts/account` |
| `api/routers/assistant.py` | 4 | `POST /api/v1/assistant/threads`, `GET /threads/{thread_id}`, `POST /threads/{thread_id}/messages`, `GET /runs/{run_id}` |
| `api/routers/recommendations.py` | 8 | `POST /api/v1/recommendations/generate`, `GET /recommendations`, `GET /recommendations/{rec_id}`, `POST /recommendations/{rec_id}/approve`, `/reject`, `/execute`, `GET /executions`, `GET /audit-logs` |

Role requirements (from `Depends(...)` in the routers): analytics reads need viewer; `POST /forecasts/account`, all assistant routes and `POST /recommendations/generate` need analyst; approve, reject, execute, `/executions` and `/audit-logs` need admin; recommendation list and detail need viewer.

## 6. Frontend modules: 12

| File | Role |
|---|---|
| `main.tsx`, `App.tsx` | Entry; organization prompt; health footer; composes the three panels |
| `Dashboard.tsx` | KPI tiles, two line charts, campaign table, keyword table, date presets |
| `Assistant.tsx` | Chat with cited claims and "data used" chips |
| `ApprovalCenter.tsx` | Recommendation list, evidence, generate, hash-bound approve/reject |
| `api.ts` | Fetch client (development tenant and role headers) |
| `types.ts`, `format.ts`, `useResource.ts` | Types, rupee/percent/date formatting, data-loading hook |
| `charts/LineChart.tsx` | Accessible SVG line chart (no chart library) |
| `components/KpiTiles.tsx`, `components/Tables.tsx` | KPI tiles; campaign and keyword tables |

Runtime dependencies: React 19 and react-dom only (`frontend/package.json`).

## 7. RAG components (8)

1. Source documents: `backend/knowledge/glossary.md` (18 chunks), `backend/knowledge/optimization_guidelines.md` (12 chunks); chunk counts measured by running `chunk_markdown` on them.
2. Chunker: `application/knowledge/chunking.py`
3. Ingestion: `application/knowledge/ingest.py`, CLI `ingest-knowledge` in `cli.py`
4. Embedders: `infrastructure/llm/openai_embeddings.py`, `hash_embedder.py`; wrappers `application/llm/embedding.py`
5. Vector store: `infrastructure/db/knowledge_store.py`, `knowledge_models.py`, migration `0005`
6. Retrieval tool: `search_knowledge` in `application/assistant/tools.py`
7. Context construction: `application/assistant/evidence.py` (sanitising, injection flag, evidence ids)
8. Faithfulness validation: `application/assistant/faithfulness.py`

## 8. LLM prompts (6, counting both nudge messages)

| Prompt | File |
|---|---|
| Assistant system prompt | `application/assistant/prompts.py:SYSTEM_PROMPT` |
| Assistant nudge | `application/assistant/prompts.py:NUDGE` |
| Supervisor (routing) prompt | `application/agents/prompts.py:SUPERVISOR_PROMPT` |
| Specialist prompt template | `application/agents/prompts.py:specialist_prompt` |
| Synthesizer prompt (system prompt plus graph addendum) and graph nudge | `application/agents/prompts.py:SYNTHESIZER_PROMPT`, `NUDGE` |

## 9. Agents and graph (summary)

| Role | Kind | Source |
|---|---|---|
| Supervisor (`plan` node) | LLM, structured output | `agents/graph.py:plan_node` |
| `campaign_analysis` | LLM tool-calling | `agents/specialists.py` |
| `keyword_intelligence` | LLM tool-calling | same |
| `performance_prediction` | LLM tool-calling | same |
| `ppc_reporting` | LLM tool-calling | same |
| Synthesizer (`synthesize` node) | LLM, forced `final_answer` tool | `agents/graph.py:synthesize_node` |
| Validator (`validate` node) | **Code, no LLM** | `agents/graph.py:validate_node` calling `assistant/faithfulness.py` |

The four specialists share **one** LangGraph node (`specialist`), which pops one name from `pending` per visit. Full detail: `AGENT_AND_LLM_MAP.md`.
