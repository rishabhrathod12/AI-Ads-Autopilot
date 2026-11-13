# Project Completion Status

Audit date: 2026-11-13, commit `b321aff`, working tree clean. Read-only audit.
Method: I read the source, migrations, tests and evaluation result files, and re-ran static checks and the test suites
that need no database. I did **not** re-run the database-backed tests, the ML evaluations or any live-model evaluation;
those figures are quoted from the project's own records and marked **[doc]**.

Re-run in this session (before the audit): ruff, ruff format, mypy strict (209 files), import-linter (2 contracts kept),
backend pytest **764 passed, 76 skipped, 48 deselected**, frontend lint, typecheck and **60 tests passed**. `npm run build` was not run.

## 1. Evidence levels

| Level | Meaning |
|---|---|
| Implemented | code exists |
| Tested | automated tests exercise it |
| Integrated | reachable from a running application path |
| Production-ready | verified in the target environment on real data and real dependencies |

**No component is production-ready.** All data is synthetic `MOCK`; no real Amazon call exists; container images, the Compose stack, nginx and restore have never been run; there is no identity provider or login screen.

## 2. Milestone numbering (read before the table)

The repository uses two numbering schemes, and the status file has stale lines:

- `docs/IMPLEMENTATION_PLAN.md` defines **M7 = Evaluation and Testing**. The commit history and the owner-facing status use **"M7" for observability and audit hygiene** (`e1e671c`, ADR 0023). Both are assessed below.
- An **Authentication step** (ADR 0021, `b8af071`) sits between M5 and M6 and has no M-number.
- `docs/PROJECT_STATUS.txt` begins with a table that still says "M6 not started", "M7 partial; no audit log table" and "M8 not started". Later lines in the same file, the commits, the migrations and the code contradict it: an `audit_logs` table exists from migration `0001`, approvals exist (`0006`), deployment files exist. **Trust the later lines and the code, not the header table.**

## 3. Milestone audit M0 - M9

Categories requested: Complete, Implemented but not verified, Partially implemented, Failed, Not started, Unknown.
Because "implemented" and "exit criteria met" differ here, each row gives both.

| M | Scope | Code status | Verified | Exit criteria | Verdict | Evidence |
|---|---|---|---|---|---|---|
| **M0** | Planning: PRD, ADRs, skeleton | Docs present: `docs/PRD.md`, `ARCHITECTURE.md`, 25 ADRs | n/a | "owner written approval" cannot be checked from the repository | **Complete** (approval **Unknown**) | `docs/`, commit `40a5dce` |
| **M1** | Foundation: scaffolds, settings, logging, Postgres + Alembic, Compose, CI, startup execution check | **Exists.** Commit `40a5dce` is titled "M0+M1" (there is no separate M1 commit). `config.py`, `logging.py`, `startup_checks.py`, migration `0001` (orgs, users, memberships, audit, RLS), `.github/workflows/ci.yml`, `docker-compose.yml` | migration and RLS verified on PG 18.6 **[doc]**; **Compose stack never run** | health endpoint (`api/main.py`) yes; cross-tenant test (`tests/integration/test_rls.py`) yes | **Implemented; Compose unverified** (mostly complete) | files named |
| **M2** | Mock data, provider port, sync, contract tests | `infrastructure/providers/mock/*`, `application/sync_service.py`, `ports/ads_provider.py`, `tests/contract/` | DB tests passed with a database **[doc]**; 764 non-DB tests pass here | reproducible dataset, provider swap by config, mock execute raises: all present | **Complete** (owner review pending per plan) | |
| **M3** | KPI, analytics, anomalies, forecasting, API, dashboard UI | all present | DB timing measured **[doc]** | **Not met.** Recorded failures: negative-term precision 0.77 vs 0.85; account forecast "no worse than seasonal-naive" 6/10 then 19/30; shipped default WAPE 0.294 vs 0.25 on the independent generator; confirmed wasted targets 0.821 vs 0.85 on economic truth. Default forecast changed to seasonal-naive by owner decision; thresholds were not loosened | **Implemented; exit gates FAILED** (known failures). Anomaly detection passes exactly at its limit (8/10) | `docs/ML_EVALUATION.md`, `tests/evals/` |
| **M4** | LLM port, cost caps, tools, pgvector knowledge base, faithfulness validator, chat API and UI | all present | live evals with `gpt-4.1-mini`: golden 29 Qs (run 1 failed the 4/4 refusal criterion, 2/4; run 2 after a prompt change 4/4), held-out 12/12; ingestion/DB tests passed **[doc]** | met on pre-registered counts, but run 2 is not held-out and samples are tiny | **Implemented, evaluated on small samples**; owner review pending | `docs/ASSISTANT.md`, `tests/evals/results/*.json` |
| **M5** | LangGraph multi-agent: supervisor, specialists, validator, checkpointing | graph, supervisor, 4 read-only specialists, synthesizer, validator, report specialist | scripted-model tests; live agent eval 19/20, routing 7/8 | "routing and faithfulness evals pass": **routing criterion R3 failed** (recorded). **No checkpointer**, no Bid/Budget agents, default engine remains `single` | **Partially implemented** | `application/agents/`, `agents_eval.json` |
| **Auth step** | JWT, org selection, roles | `infrastructure/auth/`, `api/tenancy.py` | 41 unit tests; SQL membership lookup verified **[doc]** | no IdP chosen, no provisioning, no frontend login | **Partially implemented** | |
| **M6** | Recommendations, approval, audit, gated execution | `optimization/generate.py`, `recommendations/service.py`, `domain/recommendations.py`, migration `0006`, router, Approval Center UI | SQL layer, hash-bound approval, idempotency, expiry, blocked execution: 33-check end-to-end run **[doc]** | "guardrail violations = 0; approval flow end-to-end on mock": met on mock. Not done: Bid/Budget as graph specialists, checkpointer interrupt, expiry job, OIDC login | **Implemented and DB-verified on PG 18; partially complete** | |
| **M7 (plan: evaluation and testing)** | unit/integration/agent/ML evals, security and performance tests | 840 + 48 tests; coverage 92% without DB **[doc]** | partly | "coverage and eval targets met": eval targets **not** met (M3 failures); no load or security test suite; one single-user timing run | **Partially implemented** | `tests/` |
| **M7 (owner numbering: observability)** | redaction, access log, audit for every write | `logging.py`, `application/audit.py`, `api/main.py:_access_log`, `tests/unit/test_observability.py` | SQL audit filters verified **[doc]** | not done: metrics, tracing, alerting, retention | **Implemented; partially complete** | |
| **M8** | deployment hardening | `requirements.lock`, `backend/Dockerfile`, `frontend/Dockerfile`, `nginx.conf`, `docker-compose.prod.yml`, CI audit/images/coverage jobs, `DEPLOYMENT.md`, `OPERATIONS.md` | migrations up/down and role check on PG 18 **[doc]**; **no image build, no Compose bring-up, no nginx test, no restore rehearsal, PG 16 untested** | n/a | **Implemented but not verified** | `docs/DEPLOYMENT.md` |
| **M9** | Amazon Ads API | endpoint-agnostic layer: `TokenManager`, retry + rate limiter, `LwaTokenRefresher`, `AdsHttpClient`, `TokenCipher`, `RefreshTokenVault`, `AmazonAdsApiProvider` stub, config checks | unit tests only | no eligibility, no verified API docs, **no endpoint mapping**, no SQL credential store, no OAuth flow, no real call; the layer is not wired into the app (`build_provider` refuses `amazon`) | **Partially implemented (infrastructure only); real integration Not started** | `docs/AMAZON_ADS_INTEGRATION.md` |

## 4. Feature-level status

| Feature | Implemented | Tested | Integrated | Production-ready |
|---|---|---|---|---|
| KPI engine and dashboard API | yes | yes | yes | no (MOCK) |
| Campaign health and budget analysis | yes | yes | yes | no |
| Keyword findings (statistical) | yes | yes | API + assistant tool + recommendations; **no UI** | no (precision gates partly failed) |
| Anomaly detection (CUSUM) | yes | yes | API + assistant tool; **no UI** | no |
| Forecasting (7 models) | yes | yes | API + assistant tool; **no UI**; default = seasonal-naive | no (accuracy gates failed) |
| Per-campaign forecasting | yes | yes | **no** (test-only) | no |
| Deterministic report (JSON/MD/CSV) | yes | yes | API only | no |
| Single assistant | yes | yes | yes (default engine) | no |
| Agent graph | yes | yes | only with `ASSISTANT_ENGINE=graph` | no |
| RAG over 2 documents | yes | yes | yes (needs ingest + embedding config) | no |
| Faithfulness validator | yes | yes | yes | no |
| Recommendations and approval | yes | yes | API + UI | no |
| Execution | gate only | yes | endpoint exists; always `blocked` | not applicable by design |
| JWT auth and roles | yes | yes | backend yes; **frontend no** | no |
| Audit log | yes | yes | writes yes; admin read API; **no UI** | no |
| Amazon Ads connection | partial | unit | **no** | no |
| Docker images, Compose, nginx | files | no | **never run** | no |

## 5. Gap analysis

Severity: **H** blocks a real deployment or contradicts a stated goal; **M** limits usefulness or trust; **L** hygiene.

### 5.1 Missing

| # | Gap | Sev | Evidence |
|---|---|---|---|
| M1 | **No real data path.** Only mock data; sync is reachable only through `python -m app.cli seed-mock`; no scheduler, no API sync route. | H | `cli.py`, `infrastructure/providers/factory.py` |
| M2 | **No identity provider, user provisioning or login UI.** The frontend sends `X-Dev-Org-Id` and `X-Dev-Role`; the backend honours them only outside production, so the shipped frontend cannot work against a production-configured backend. | H | `frontend/src/api.ts`, `api/tenancy.py`, `startup_checks.py` |
| M3 | **No Bid or Budget agent and no LLM-assisted recommendations.** Proposals are pure rules. The assistant has no tool that reads `recommendations`, so it cannot explain a pending proposal. | M (H if "AI-assisted recommendations" is a requirement) | `agents/specialists.py` docstring and tool list; `optimization/generate.py` |
| M4 | **No LangGraph checkpointer and no human-approval interrupt node.** Approval is a separate REST workflow, not part of the graph. | M | no `checkpointer` in `app/`; plan step 5 deferral |
| M5 | **RAG is minimal:** 2 documents / 30 chunks, exact scan with no ANN index, no hybrid search, no reranker, no score threshold, no retrieval metric (recall@k), no upload route for tenant `seller_note` documents although the schema supports them. | M | `knowledge_store.py:_SEARCH`, `cli.py` |
| M6 | **No trained or versioned model artifact, no training pipeline, no model registry.** Acceptable while forecasts are fitted per request, but ML claims should be read as "statistics and per-request fits". | M | section 0 of `ML_COMPONENT_MAP.md` |
| M7 | **ML accuracy gates failed** (section 3, M3) and **no real-account evidence exists**. | H for any accuracy claim | `docs/ML_EVALUATION.md` |
| M8 | **No UI** for anomalies, keyword findings, forecasts, reports, executions or audit logs. | M | `frontend/src/api.ts` |
| M9 | **No automation:** forecasts and recommendations are created only by manual POST; recommendations expire only lazily when someone tries to decide on them. | M | `routers/analytics.py`, `recommendations/service.py` |
| M10 | **No metrics, tracing or alerting;** assistant rate limiter is per process; spend-cap check can be overshot by about one call under concurrency (documented). | M | `api/ratelimit.py`, `llm/budget.py` docstring |
| M11 | **SQL credential store for Amazon tokens not built;** no OAuth consent flow. | H for M9 | `ports/credential_store.py` docstring |
| M12 | Docker images, Compose, nginx, restore, PostgreSQL 16 and load under concurrency never verified. | H for deployment | `PROJECT_STATUS.txt` |

### 5.2 Disconnected or unused in application paths

Referenced only by tests (verified by searching `backend/app` and `backend/tests`):

| Component | File | Note |
|---|---|---|
| `forecast_campaigns` | `application/forecasting/reports.py` | per-campaign forecasts have no endpoint, tool or UI |
| `compare_periods`, `aggregate_period` | `domain/kpi/periods.py` | exercised by tests only |
| `clicks_needed_to_rule_out` | `domain/stats.py` | helper unused outside tests |
| Whole Amazon layer: `AdsHttpClient`, `TokenManager`, `LwaTokenRefresher`, `RefreshTokenVault`, `TokenCipher`, `AmazonAdsApiProvider` | `application/ads_api/`, `infrastructure/ads_api/`, `infrastructure/providers/amazon/` | by design disabled and unwired (ADR 0025), 4 files plus provider; config validation is the only live part |
| `HashingEmbedder` | `infrastructure/llm/hash_embedder.py` | dev/test stand-in, non-semantic, by design |
| Learned forecasters ETS / ridge / gbm | `forecasting/models.py` | backtested on every forecast but never selected (`LEARNED_MODELS_SELECTABLE = False`); cost compute on every POST |

### 5.3 Duplicated or overlapping

| Item | Detail |
|---|---|
| Two assistant engines | `assistant/loop.py:run_assistant` and `agents/graph.py` both implement a tool loop and share `check_final`; the graph reuses the loop's validator and `RunState`. The default remains `single`. Behaviour on the same question can differ and both need evaluating. |
| Two nudge prompts | `assistant/prompts.py:NUDGE` and `agents/prompts.py:NUDGE` |
| Milestone numbering | two meanings of "M7" (section 2) |

### 5.4 Documentation defects (found by reading the code)

| Defect | Where |
|---|---|
| Adapter docstring says it "has NOT been called against the live API", but stored eval results show real runs | `infrastructure/llm/openai_client.py` lines 1-10 |
| `PROJECT_STATUS.txt` header table is stale (M6, M7, M8) | `docs/PROJECT_STATUS.txt` |
| `LLMClient` port docstring says "the API streams events (tool calls, tool results, the validated final answer)", but no streaming endpoint or event stream exists (no `StreamingResponse` or SSE in `backend/app`) | `application/ports/llm.py` |
| Stored golden-run JSON predates the offline re-score of group A (JSON: 27/29 and 25/29 pass; `docs/ASSISTANT.md` reports group A 14/14 after a checker fix) | `tests/evals/results/assistant_golden*.json` vs `docs/ASSISTANT.md` |
| Plan says "306 tests pass locally" for M3; the suite is now 840 + 48 evals | `docs/IMPLEMENTATION_PLAN.md` |

### 5.5 Security and hygiene observations

| Observation | Sev |
|---|---|
| `users` has no tenant column or RLS; the app role can SELECT all rows. No route reads it except the membership join, but a future route could. | M |
| In development mode any caller may claim `X-Dev-Role: owner`; safe only because production refuses to start in that mode. | L (by design) |
| `backend/.coverage` is a tracked file in git. | L |
| Long-lived DB transaction held open during the model call (`docs/ASSISTANT.md`); fine locally, will not scale. | M |
| Faithfulness validator checks magnitude, unit, sign and dates, not meaning ("rose" vs "fell"), and not retrieval relevance. | M |
| `assistant_engine=graph` and live LLM paths have only small single-sample evaluations. | M |
| Tool `get_account_forecast` returns "none" until a manual POST; a seller asking about the forecast gets "no forecast exists" by default. | L |

## 6. What is real today, in one paragraph

A tested, tenant-isolated analytics and approval backend with a React dashboard, running on a synthetic dataset, that can compute KPIs, health, keyword
findings, change-point anomalies and a baseline forecast; propose guard-railed bid, budget and negative-keyword changes; let an admin approve them with a
hash-bound decision; record everything in an insert-only audit log; and refuse, in several independent places, to send anything to Amazon. On top sits a
read-only, claim-validated LLM assistant (single engine by default, LangGraph supervisor/specialist engine optional) that can search two reference documents
through pgvector. The data is not real, the accuracy gates for the statistical and forecasting components are partly failed, deployment artifacts have not
been exercised, and there is no login, so nothing here is production-ready.

## 7. Suggested next steps (not performed; each needs your decision)

1. Decide the identity provider and add a sign-in flow, or the frontend cannot reach a production backend.
2. Run the Docker build and Compose bring-up on a machine with Docker (or push to CI) and record the result.
3. Decide whether "AI-assisted recommendations" means LLM explanations of existing proposals (needs a read tool for `recommendations`) or LLM-generated proposals (needs a Bid/Budget agent and the approval-interrupt design).
4. Decide the fate of the failed M3 gates (keep as known failures, or pre-register new fresh-seed evaluations as the project's own rules require).
5. Obtain Amazon Ads API access and verified documentation before any M9 work.
6. Fix the three documentation defects in 5.4 and the stale `PROJECT_STATUS.txt` header.
