# Complete Project Architecture

Audit date: 2026-11-13, commit `b321aff`. Read-only audit; no application code or configuration was changed.
Companion files: `PROJECT_MODULE_INVENTORY.md` (counts), `ML_COMPONENT_MAP.md`, `AGENT_AND_LLM_MAP.md`,
`PROJECT_COMPLETION_STATUS.md` (milestones and gaps). Evidence levels (implemented / tested / integrated /
production-ready) are defined in the inventory file. **Nothing here is production-ready**: the data is synthetic
(`MOCK`), no real Amazon call exists, and container images and the Compose stack have never been built.

## 1. Shape of the system

A modular monolith (ADR 0001): a FastAPI backend, a React single-page frontend, PostgreSQL with pgvector. The backend is split
into four layers and the dependency rule is machine-checked (import-linter, 2 contracts kept):

```
api  ->  application  ->  domain
  \          ^
   \-> infrastructure (implements application/ports)
```
- `domain/` is framework-free (KPI maths, CUSUM, statistics, recommendation lifecycle and guardrails).
- `application/` holds use cases and 15 `Protocol` ports (provider, stores, LLM, embedding, auth ...).
- `infrastructure/` holds the concrete adapters: SQLAlchemy stores, OpenAI clients, mock and Amazon providers, JWT verifier.
- `api/` holds FastAPI routers, tenancy and role dependencies.

Money is stored as integer paise (ADR 0004). All business numbers come from deterministic Python (ADR 0005).

## 2. End-to-end flow: Amazon data to the seller dashboard

```
 [1 Source]            [2 Provider port]        [3 Sync]             [4 Store (Postgres, RLS)]
 MockGenerator   ->    MockAdvertisingData  ->  SyncService     ->   SqlAdsStore  ->  8 ads tables
 (seeded numpy)        Provider                 (validate,           (upsert, no        + report_runs
 AmazonAdsApiProvider  (ports/ads_provider)     quarantine,          DELETE grant)
 (stub, not wired)                              provenance)
                                                      ^ only entry point: CLI `seed-mock`

 [5 Read model]            [6 Analytics / ML]               [7 API]                 [8 Frontend]
 SqlPerformanceReader  ->  dashboard, campaigns,       ->   /api/v1/... routers ->  Dashboard.tsx
 (tenant session, RLS)     keywords, anomalies,             (role-guarded,           ApprovalCenter.tsx
                           forecasts, reports,              tenant-scoped)           Assistant.tsx
                           recommendations
                                       \-> [9 Assistant: tools -> evidence -> LLM -> validator] -> /assistant/*
```

### Step by step (with files)

1. **Source data.** Only synthetic data exists. `infrastructure/providers/mock/generator.py` builds a seeded, bottom-up dataset: per-search-term latent behaviour; targets, ad groups and campaigns are exact sums; planted scenarios (wasted keywords, irrelevant terms, winning auto terms, a high-ACOS campaign, a budget-capped winner, a campaign whose conversion rate halves for the last 14 days, and a 3-day attribution lag). Every record is stamped `MOCK`. 646,436 metric rows for the default 120-day seed **[doc: PROJECT_STATUS.txt]**.
2. **Provider port.** `application/ports/ads_provider.py:AdvertisingDataProvider` (profiles, campaigns, ad groups, targets, search terms, streamed daily metrics, `execute_change`). `ADS_PROVIDER=mock` is the only value that builds a provider (`infrastructure/providers/factory.py`); `amazon` raises `ProviderNotAvailableError`. `AmazonAdsApiProvider` exists but every read raises "endpoint mapping is not implemented" and `execute_change` always raises `ExecutionNotPermittedError`.
3. **Sync.** `application/sync_service.py:SyncService.sync` pulls entities in order (campaign, ad group, target, search term), then metrics; records with a wrong `data_source`, a missing parent or a bad row are **quarantined**, counted on a `report_runs` row, never fixed silently. **It is invoked only by `python -m app.cli seed-mock`.** There is no scheduled job and no API route that syncs (ADR 0009 background jobs is a decision record only).
4. **Storage.** `infrastructure/db/ads_store.py:SqlAdsStore` upserts into `seller_accounts`, `advertising_profiles`, `campaigns`, `ad_groups`, `keywords_targets`, `search_terms`, `report_runs`, `performance_daily` under a tenant session.
5. **Read model.** `infrastructure/db/performance_reader.py:SqlPerformanceReader` implements `PerformanceReader`. It exposes freshness (latest day, *mature-through* day), entity totals, daily series and provenance. Immature days (the last ~3, still inside Amazon's attribution window) are excluded from every judgement.
6. **Analytics and ML.** Deterministic KPI engine (`domain/kpi`), health and budget rules (`analytics/campaigns.py`), statistical keyword findings (`analytics/keywords.py`), CUSUM anomalies (`analytics/anomalies.py`), forecasting (`forecasting/`), deterministic report (`reporting/report.py`), rule-based recommendations (`optimization/generate.py`). See `ML_COMPONENT_MAP.md`.
7. **API.** 25 endpoints (inventory section 5). Every data route resolves a `Principal` (JWT or development header), checks a minimum role, opens a tenant session (`SET LOCAL ROLE adpilot_app` and `set_config('app.org_id', ..., true)`), and returns an envelope with provenance, freshness and a MOCK banner.
8. **Frontend.** `frontend/src/App.tsx` asks for an organization id (development only), then renders `Dashboard`, `ApprovalCenter`, `Assistant`, and a footer showing `/health` (data source MOCK, execution disabled).
9. **Assistant path (optional).** A question goes to `/api/v1/assistant/threads/{id}/messages`; the engine (single or graph) lets the LLM call read-only tools that wrap steps 5-6; each result becomes **evidence**; the LLM returns cited claims; code validates them; only validated claims are shown. See section 6 and `AGENT_AND_LLM_MAP.md`.

### What is automatic and what is manual
| Step | Trigger today |
|---|---|
| Load data | manual CLI (`seed-mock`) |
| Ingest knowledge documents | manual CLI (`ingest-knowledge`) |
| Create a forecast | manual `POST /api/v1/forecasts/account` |
| Generate recommendations | manual `POST /api/v1/recommendations/generate` (button in Approval Center) |
| Expire recommendations | lazily, when someone tries to decide on one (`recommendations/service.py:decide`); no expiry job |
| Anything that reaches Amazon | impossible in this build |

## 3. Machine learning, LLM and agents (pointers)
- ML: 7 forecasters, 2 CUSUM detectors, 2 exact-binomial procedures, 0 trained artifacts. `ML_COMPONENT_MAP.md`.
- LLM: OpenAI chat and embeddings, one adapter each, wrapped with retries and per-tenant spend caps. `AGENT_AND_LLM_MAP.md`.
- Agents: one LangGraph graph (4 nodes) with supervisor, 4 specialists, synthesizer, code validator; plus the default single-assistant loop.

## 4. Components by layer (see inventory for file lists)
| Layer | Responsibility | Examples |
|---|---|---|
| domain | pure rules | `kpi/metrics.py`, `anomaly.py`, `stats.py`, `recommendations.py` |
| application | use cases | `analytics/*`, `forecasting/*`, `optimization/generate.py`, `assistant/*`, `agents/*` |
| infrastructure | adapters | `db/*`, `llm/*`, `providers/*`, `ads_api/*`, `auth/*` |
| api | HTTP | `main.py`, `tenancy.py`, `routers/*` |

## 5. Agent architecture (summary)
See `AGENT_AND_LLM_MAP.md` section 5 for the per-agent table. In short: `plan` (supervisor, LLM, JSON plan) -> `specialist` (loops over the chosen specialists, each an LLM with a restricted tool list) -> `synthesize` (LLM, only `final_answer`) <-> `validate` (code). Routes live in `GraphRun.next_step`, `after_synthesize`, `after_validate`. No checkpointer, no human-in-the-loop node, no write tools.

## 6. RAG architecture

| Stage | Implementation | Evidence |
|---|---|---|
| **Documents** | Two markdown files authored by the team: `backend/knowledge/glossary.md`, `backend/knowledge/optimization_guidelines.md`; both state they are not Amazon documentation. Source types: `glossary`, `guideline`, `seller_note` (tenant-owned notes are supported by the schema but **no upload endpoint exists**). | `backend/knowledge/`, `cli.py:knowledge_documents` |
| **Ingestion** | CLI `python -m app.cli ingest-knowledge`. Idempotent: skips a document whose SHA-256 and embedding model are unchanged; otherwise replaces the document and its chunks. Runs on the owner DB connection because only the owner may write global (`organization_id IS NULL`) documents. | `application/knowledge/ingest.py`, `cli.py:ingest_knowledge` |
| **Chunking** | Markdown-heading aware; heading path kept ("A > B"), paragraphs packed up to 900 characters, long paragraphs split on sentence ends, hard-wrap as last resort. Measured: glossary 18 chunks, guidelines 12 chunks (max 366 chars). The heading is embedded with the content. | `application/knowledge/chunking.py` |
| **Embeddings** | `EmbeddingClient` port. OpenAI Embeddings with `dimensions=1536` (the column width), or an offline hashed bag-of-words (not semantic; dev/test only). Retries and per-tenant spend cap for query-time calls; ingestion is retried but not tenant-metered. | `infrastructure/llm/openai_embeddings.py`, `hash_embedder.py`, `application/llm/embedding.py`, `infrastructure/llm/factory.py` |
| **Vector database** | PostgreSQL + pgvector, `knowledge_chunks.embedding vector(1536)`, `embedding_model` stored per chunk. Search compares only chunks embedded with the same model. | `migrations/versions/0005_knowledge.py`, `infrastructure/db/knowledge_models.py` |
| **Retrieval** | Exact cosine nearest neighbours: `ORDER BY embedding <=> query LIMIT n` (n = 1-6, default 4). **No ANN index (no HNSW/IVFFlat), no hybrid keyword search, no reranker, no minimum-score cut-off.** Fine for 30 chunks; would need an index at scale. Tenant filter: global chunks plus the caller's own. | `infrastructure/db/knowledge_store.py:_SEARCH`, `assistant/tools.py:_knowledge` |
| **Context construction** | Retrieved passages become one **evidence** record: `{title, source, section, scope, passage}` plus a note "Reference material, not the seller's data". `passage` is sanitised, capped at 1,200 chars and flagged if it looks like an injection. The model sees it as a *tool-role* message. | `assistant/evidence.py`, `assistant/tools.py:_knowledge` |
| **Generation** | The LLM writes `final_answer` claims citing the passage's evidence id; the system prompt tells it passages are not the seller's figures, to name the title, and to say "no reference" when nothing fits. | `assistant/prompts.py` |
| **Faithfulness validation** | `validate_answer` (code): every number, unit, sign and date in each claim must appear in a **cited** evidence record; derived figures are rejected; number words (two..twenty) are checked; numbers inside passage text count as text facts. One correction round, then unsupported claims are dropped. It does **not** check meaning (e.g. "rose" vs "fell") or that the retrieved passage is relevant. | `assistant/faithfulness.py`, `assistant/loop.py:check_final` |
| **Tool availability** | `search_knowledge` is offered only if `EMBEDDING_PROVIDER` is not `none` and the store is configured; otherwise the assistant answers from account data only. | `api/routers/assistant.py:make_service` |

RAG evaluation **[doc]**: golden group B (4 reference questions; criterion: at least 3 use the knowledge base) scored 4/4 in both runs (`docs/ASSISTANT.md`); no retrieval-quality metric (recall@k, MRR) exists and no ingestion has been run against a real database in this audit. Gaps are listed in `PROJECT_COMPLETION_STATUS.md`.

## 7. Database architecture

**Engine:** PostgreSQL (verified on 18.6 with pgvector 0.8.7 **[doc]**; CI and Compose target PostgreSQL 16, **not verified**). Async SQLAlchemy 2.0 + asyncpg; Alembic migrations `0001`-`0006`; the migration chain was reported verified up/down and equal to the ORM **[doc: PROJECT_STATUS.txt]**.

### 7.1 Tables (22) and relationships

| Table | Key relations | Tenant column | RLS | App-role grants |
|---|---|---|---|---|
| `organizations` | root | `id` | FORCE, policy on `id` | SELECT, INSERT, UPDATE |
| `users` | referenced by `memberships` | **none** | **none** | SELECT, INSERT, UPDATE |
| `memberships` | `user_id -> users`, `organization_id -> organizations`; unique (user, org); `role` | `organization_id` | FORCE | SELECT, INSERT, UPDATE |
| `audit_logs` | `organization_id -> organizations`; JSONB `before`/`after`, `request_id` | yes | FORCE | **SELECT, INSERT only** |
| `seller_accounts` | `organization_id` | yes | FORCE | SELECT, INSERT, UPDATE |
| `advertising_profiles` | `seller_account_id -> seller_accounts` | yes | FORCE | same |
| `campaigns` | `profile_id -> advertising_profiles` | yes | FORCE | same |
| `ad_groups` | `campaign_id -> campaigns` | yes | FORCE | same |
| `keywords_targets` | `ad_group_id -> ad_groups`; text, match type, bid, `is_negative` | yes | FORCE | same |
| `search_terms` | `ad_group_id -> ad_groups`, `target_id -> keywords_targets` (nullable) | yes | FORCE | same |
| `report_runs` | `profile_id -> advertising_profiles`; counts rows and quarantined rows | yes | FORCE | same |
| `performance_daily` | PK (organization, entity_type, entity_id, date); `report_run_id -> report_runs`; `entity_id` is polymorphic (campaign / ad group / target / search term), **no FK**; CHECKs on clicks <= impressions, orders <= clicks | yes | FORCE | same |
| `forecast_runs` | scope (account/campaign), metric, horizon, model, `used_baseline`, JSONB `params` and `points` | yes | FORCE | SELECT, INSERT |
| `assistant_threads` | root of a conversation | yes | FORCE | SELECT, INSERT |
| `assistant_runs` | `thread_id -> assistant_threads`; status, evidence, trace, violations, tokens, cost | yes | FORCE | SELECT, INSERT |
| `assistant_messages` | `thread_id`, `run_id` (nullable) | yes | FORCE | SELECT, INSERT |
| `llm_usage` | per-tenant spend ledger (chat and embeddings) | yes | FORCE | SELECT, INSERT |
| `knowledge_documents` | nullable `organization_id`; unique source key per scope (partial unique indexes) | nullable | **ENABLE only**, policy `read_global_or_own` (SELECT) | SELECT |
| `knowledge_chunks` | `document_id -> knowledge_documents` ON DELETE CASCADE; `embedding vector(1536)`; denormalised `organization_id` | nullable | ENABLE only, `read_global_or_own` | SELECT |
| `recommendations` | unique (organization, `content_hash`); CHECKs on type, status, data source, expiry | yes | FORCE | SELECT, INSERT, and **UPDATE (status) column only** |
| `approval_records` | one immutable decision per recommendation; stores the approved hash | yes | FORCE | SELECT, INSERT |
| `execution_attempts` | append-only; `status` is always `blocked` in this build | yes | FORCE | SELECT, INSERT |

(The three tables per migration group, RLS loop lists and grants are in `migrations/versions/0001`-`0006`; policies use
`organization_id = nullif(current_setting('app.org_id', true), '')::uuid`.)

### 7.2 Tenant isolation mechanism
1. Each request opens a transaction through `infrastructure/db/session.py:tenant_session`: `SET LOCAL ROLE adpilot_app` (a `NOLOGIN NOSUPERUSER NOBYPASSRLS` role created in `0001`) then `set_config('app.org_id', <uuid>, true)` (transaction-local, so a pooled connection cannot leak it).
2. Row-level security with `FORCE` applies the policy even to the table owner.
3. Production startup runs `assert_rls_enforced` and **refuses to serve** if the effective role is a superuser or has BYPASSRLS.
4. Least privilege: no DELETE anywhere for the app role; `audit_logs`, `approval_records`, `execution_attempts` insert-only; recommendations may change only `status`.
5. Tests: `tests/integration/test_rls.py` and the store tests; the end-to-end run reports cross-tenant isolation and "app role cannot tamper" **[doc]**.

Observations: `users` has no tenant column and no RLS (it is an identity table; the app role can SELECT every row, although no route queries it other than the membership join). Knowledge tables are `ENABLE` not `FORCE` on purpose so the owner CLI can write global documents.

### 7.3 Migration status
| Migration | Content | Verification |
|---|---|---|
| 0001 foundation | orgs, users, memberships, audit_logs, app role, RLS, grants | up/down verified on PG 18.6 **[doc]** |
| 0002 advertising data | 8 tables | same |
| 0003 forecast_runs | `clock_timestamp()` ordering (found by a flaky test) | same |
| 0004 assistant | 4 tables | same |
| 0005 knowledge | `CREATE EXTENSION vector`, 2 tables | same; PG 16 not verified |
| 0006 recommendations | 3 tables, column-level grant | same |

## 8. Frontend architecture

Stack: React 19, Vite, TypeScript, no UI or chart library (a hand-written accessible SVG `LineChart`). One page, three panels. No router, no global state library; `useResource` fetches with abort signals.

| Panel / file | What it shows | Backend calls (`frontend/src/api.ts`) |
|---|---|---|
| `App.tsx` | organization-id prompt (stored in `localStorage` when available); health footer | `GET /health` |
| `Dashboard.tsx` + `KpiTiles`, `LineChart`, `Tables` | MOCK banner, 7/28/90-day presets, KPI tiles with comparisons, two charts (spend/sales, ACOS vs target; immature days dashed), campaign health table, top-keyword table | `GET /api/v1/dashboard/summary`, `/dashboard/trends`, `/campaigns`, `/keywords?limit=15` |
| `Assistant.tsx` | question box, answer as cited claims, "data used" chips, caveats | `POST /api/v1/assistant/threads`, `POST /threads/{id}/messages` |
| `ApprovalCenter.tsx` | recommendation list by status, evidence, "Generate", approve/reject with confirmation bound to the content hash, development-only role menu | `GET /api/v1/recommendations`, `POST /recommendations/generate`, `POST /recommendations/{id}/approve|reject` |

**Backend capabilities with no UI:** campaign changes, search terms, keyword findings, anomalies, forecasts (create and read), performance report (JSON/Markdown/CSV), recommendation detail, execute, executions list, audit logs, thread and run read-backs. **No sign-in screen**: the frontend sends `X-Dev-Org-Id` and `X-Dev-Role`, which the backend honours only when real authentication is not configured and never in production (`frontend/src/api.ts`, `api/tenancy.py`). So the frontend cannot talk to a production-configured backend today.
The assistant panel does not select an engine (server-side setting). 60 frontend tests cover all components.

## 9. Security architecture

| Control | Implementation | Evidence | Verified |
|---|---|---|---|
| Authentication | Bearer JWT verified against a provider's JWKS; **asymmetric algorithms only** (`alg: none` and HMAC rejected); issuer, audience, `exp`, `sub`, `iss`, `aud` required; 30 s leeway; token length cap; failure reasons never returned. Provider-independent. | `infrastructure/auth/jwt_verifier.py`, `api/tenancy.py:_verified_principal` | 41 unit tests **[doc]**; **no identity provider chosen, no user provisioning, no login UI** |
| Auth modes | Configured -> JWT required and the dev header is ignored. Not configured -> dev header allowed, **but production refuses to start** (`validate_auth_readiness`) and `resolve_tenant` refuses in production as well. A partial configuration is refused everywhere. | `startup_checks.py`, `api/tenancy.py` | tests |
| Authorization | Ordered roles viewer < analyst < admin < owner; routes declare the minimum role via dependency; role comes from the `memberships` row, not the token; non-member gets 403. | `domain/auth.py`, `api/tenancy.py:require_role`, `infrastructure/auth/membership.py` | unit + SQL tests |
| **Development caveat** | In development mode any caller may send `X-Dev-Role: owner`. Acceptable only because production refuses this mode. | `api/tenancy.py:_dev_principal` | by design |
| Multi-tenant isolation | RLS (section 7.2) plus tenant-scoped stores that also filter by `organization_id`. | see 7.2 | DB tests **[doc]** |
| Audit logs | Insert-only `audit_logs`; every write path records actor, action, entity, before/after, request id, with credentials and emails scrubbed. Actions seen in code: `recommendations.generated`, `recommendation.created/approved/rejected/expired`, `execution.blocked`, `forecast.created`, `assistant.thread_created`, `assistant.question_asked`. Readable by admins at `GET /audit-logs` (filters). | `application/audit.py`, routers | unit tests; SQL filters verified **[doc]**. **Not audited:** reads (by design), failed logins (no login), sync runs (CLI) |
| Human approval | Recommendations are proposals only. Approve/reject needs **admin**, must carry the SHA-256 `content_hash` the person reviewed, recomputed server-side (tamper check), is idempotent only for the identical decision by the same person, and writes status, approval record and audit entry in one transaction with a row lock. Recommendations expire after 7 days; an approval would be good for 24 h. | `domain/recommendations.py`, `application/recommendations/service.py` | unit + SQL tests **[doc]** |
| Execution safeguards | (1) `AD_EXECUTION_ENABLED=false` default; (2) startup refuses `true` with the mock provider or when approval service, authorization checks and audit sink prerequisites are not declared, and `create_app` never declares them, so it cannot be enabled by configuration; (3) `POST /recommendations/{id}/execute` always stores a `blocked` attempt, audits it and returns 409; (4) `AmazonAdsApiProvider.execute_change` always raises; (5) the HTTP client refuses `mutating=True` requests unless built with `mutations_allowed=True`, which nothing does; (6) mock `execute_change` must raise. | `startup_checks.py`, `recommendations/service.py:_block_reason`, `infrastructure/providers/amazon/provider.py`, `infrastructure/ads_api/http_client.py` | tests; **no live execution path exists to test** |
| Secrets | `SecretStr` settings; `.env` git-ignored; log redaction at value level; Amazon refresh tokens encrypted with a keyed cipher (key rotation by id) **but the SQL credential store and OAuth flow are not built**. | `config.py`, `logging.py`, `infrastructure/ads_api/token_cipher.py`, `application/ports/credential_store.py` | unit |
| LLM safety | see `AGENT_AND_LLM_MAP.md` section 6: redaction, read-only tools, data-not-instructions, spend caps, validator. | | evals |
| Transport/API hygiene | CORS limited to GET/POST and named headers; OpenAPI docs disabled in production; request-id sanitised; access log omits query string, headers and bodies; per-tenant assistant rate limit (in-process, **not shared across workers**). | `api/main.py`, `api/ratelimit.py` | tests |
| Supply chain / CI | hash-pinned `requirements.lock`, pip-audit and npm audit jobs, gitleaks job, coverage 92% without DB **[doc]** | `.github/workflows/ci.yml` | **CI image/compose jobs never run here** |

## 10. Worked flows

Each flow lists the files and tables that participate. "ML" means a statistical or learned algorithm; "LLM" means a model call.

### Flow A. Seller campaign performance analysis

*Question: "How are my campaigns doing and which need attention?"*

**A1. Through the dashboard (no LLM, no learned model).**
1. `frontend/src/Dashboard.tsx` calls `analytics.summary/trends/campaigns/keywords` (`frontend/src/api.ts`).
2. `api/routers/analytics.py` routes `/dashboard/summary`, `/dashboard/trends`, `/campaigns`, `/keywords`; `api/tenancy.py:get_scope` requires viewer, opens the tenant session.
3. `infrastructure/db/performance_reader.py:SqlPerformanceReader` reads **`performance_daily`** (campaign level), **`campaigns`**, **`keywords_targets`**.
4. `application/analytics/dashboard.py:dashboard_summary` -> `domain/kpi/metrics.py` (ACOS, ROAS, CTR, CPC, CVR) and `domain/kpi/periods.py` (mature-day period and previous-period comparison), `domain/kpi/explain.py` (which factor moved ACOS).
5. `application/analytics/campaigns.py:campaign_overview` -> `assess_health` (needs 100 clicks; ACOS vs 30% target and 1.3x watch band) and `assess_budget` (budget-capped days share) -> `CampaignRow`s.
6. Envelope with provenance, freshness and the MOCK notice returns to React; `KpiTiles`, `LineChart`, `CampaignTable` render.
- **ML:** none. **LLM:** none. **Agents:** none. **Tables read:** `performance_daily`, `campaigns`, `keywords_targets`.

**A2. Through the assistant (adds LLM, optional anomaly statistics).**
1. `Assistant.tsx` -> `POST /api/v1/assistant/threads` (writes `assistant_threads`, audit `assistant.thread_created`) then `.../messages` (analyst role; rate limit; writes `assistant_runs`, `assistant_messages`, `llm_usage`, audit `assistant.question_asked`).
2. `assistant/service.py:ask` redacts the question, loads the last 6 messages, runs the engine.
3. Graph engine: supervisor picks `campaign_analysis`; it calls `get_dashboard_summary`, `get_campaign_overview`, `get_campaign_changes`, `get_anomalies` (the last runs `application/analytics/anomalies.py` with **Bernoulli/Gaussian CUSUM** over `performance_daily`). Single engine: the same tools chosen by one model.
4. `assistant/tools.py:ToolRunner` wraps each result into evidence (`ev_1`, ...).
5. Synthesizer LLM -> `final_answer` claims -> `faithfulness.py` validates -> reply stored and returned.
- **ML:** CUSUM (if anomalies are called). **LLM:** `gpt-4.1-mini` (or whatever `LLM_MODEL` is). **Agents:** supervisor, `campaign_analysis`, synthesizer, validator. **Tables:** the A1 tables plus the four assistant tables.

### Flow B. Keyword optimisation

*Question: "Which keywords waste money and what should I do?"*

1. **Findings** - `GET /api/v1/keywords/findings` (`routers/analytics.py`) -> `application/analytics/keywords.py:keyword_analysis`. Reads `campaigns`, `keywords_targets`, `search_terms`, and mature-day totals from `performance_daily` at target, search-term and campaign level. Algorithms: exact binomial lower tail (`domain/stats.py:chance_of_at_most`) for zero-order waste (5% confirmed / 20% watch) and for underperformers; Clopper-Pearson lower bound (`exact_lower_bound`) for opportunities. Reference conversion rate = campaign rate (>= 100 clicks) else account rate. **No LLM.**
2. **Explanation (optional)** - the assistant with specialist `keyword_intelligence` calls `get_keyword_findings`, `get_top_keywords` and `search_knowledge`. The knowledge call embeds the query (embedding model), runs the pgvector search over `knowledge_chunks` (guidance from `optimization_guidelines.md`), and the answer cites the passage. LLM, RAG and the validator participate; findings numbers are validated against the evidence.
3. **Proposal** - `POST /api/v1/recommendations/generate` (analyst) -> `optimization/generate.py:generate_recommendations` takes only **confirmed** negative candidates and emits `negative_keyword` recommendations with an evidence dict and a content hash; guardrails drop invalid ones. Persisted to **`recommendations`**, audited as `recommendation.created` and `recommendations.generated` in **`audit_logs`**.
4. **Human decision** - `ApprovalCenter.tsx` shows the proposal and its evidence; an **admin** approves with the hash (`POST /recommendations/{id}/approve`) -> `approval_records` row, status `approved`, audit entry.
5. **Execution** - `POST /recommendations/{id}/execute` -> `execution_attempts` row `blocked` with reason `execution_disabled`, audit `execution.blocked`, HTTP 409. **Nothing reaches Amazon.**
- **ML/statistics:** binomial tail, Clopper-Pearson. **LLM:** only in step 2. **RAG:** step 2. **Tables:** `campaigns`, `keywords_targets`, `search_terms`, `performance_daily`, `knowledge_chunks`, `knowledge_documents`, `recommendations`, `approval_records`, `execution_attempts`, `audit_logs`, assistant tables, `llm_usage`.
- Caveat the project documents: "confirmed" means a weak converter at the campaign's average rate (negative-term precision 0.77 vs 0.85 bar), not proof the term is irrelevant.

### Flow C. Bid and budget recommendations (the "AI-assisted" claim, as built)

1. `POST /api/v1/recommendations/generate` -> `generate_recommendations(reader, org, period, now, target_acos_pct=30)`.
2. **Bids:** for each enabled non-negative target from `keywords_targets` with >= 30 clicks and >= 3 orders in the mature window, `propose_bid_change` scales the bid by target/actual ACOS, clamped to +/-20%, and skips changes under 5%. Inputs from `performance_daily` (target level) and `keywords_targets.bid_paise`.
3. **Budgets:** `campaign_overview` rows: HEALTHY and budget-constrained -> +20% daily budget; UNHEALTHY -> -20%; inputs `campaigns.daily_budget_paise`.
4. Each proposal gets a deterministic id (`uuid5` of org + content hash), `data_source` MOCK, 7-day expiry, rationale text built by Python (an f-string, not a model), evidence, confidence from order count, and a direction-only "expected impact".
5. Guardrails (`domain/recommendations.py:guardrail_violations`): cap 20%, positive values, expiry after creation, known data source; violators are dropped and counted.
6. Stored in `recommendations`; approval and blocked execution as in Flow B steps 4-5.
- **ML models:** none (deterministic rules). **LLM:** none anywhere in this flow. **Agents:** none. The forecast (`forecast_runs`) is **not** an input to bid or budget proposals.
- **Honest reading of "AI-assisted":** the assistant cannot see recommendations (no tool reads `recommendations`), and no agent proposes bids or budgets. The LLM can only describe account data that other tools return. If AI-assisted proposals are a product requirement, that part is not built.

### Flow D (bonus). Forecast
`POST /api/v1/forecasts/account` (analyst) -> `forecasting/reports.py:forecast_account` -> reads daily account series from `performance_daily` (mature days) -> `forecast_series` backtests 7 forecasters, selects (default seasonal-naive), adds 80% intervals, derives ACOS -> `forecast_runs` rows (spend, sales) + audit `forecast.created`. Read back by `GET /forecasts/account` or the assistant tool `get_account_forecast` (specialists `performance_prediction`, `ppc_reporting`). No UI.
