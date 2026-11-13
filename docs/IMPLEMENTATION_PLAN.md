# Implementation Plan

Milestone-gated: each milestone needs explicit owner approval before the next starts.

## Owner decisions (open, with proposed defaults)
| # | Question | Proposed default |
|---|---|---|
| 1 | Ads API eligibility/availability for India account; start check in parallel? | Yes, start in parallel |
| 2 | Marketplace/currency | Amazon.in, INR; schema carries currency + marketplace |
| 3 | Ad types | Sponsored Products only |
| 4 | LLM provider and monthly cost ceiling | OpenAI behind `LLMClient` port; ceiling TBD |
| 5 | Auth | Managed (Supabase Auth or Auth0) |
| 6 | Hosting/DB | Supabase Postgres; Docker Postgres locally |
| 7 | Docker Desktop installable? | Required for Compose; else hosted dev DB |
| 8 | Multi-tenancy timing | Orgs/memberships/RLS from M1 |
| 9 | Portfolio-only or real sellers soon? | Portfolio first |
| 10 | Repo host/visibility | Monorepo on GitHub, private initially |
| 11 | Frontend polish level | Functional + clean |
| 12 | Report formats | PDF + CSV |

## M0 – Planning (COMPLETE)
Docs + ADRs + repo skeleton. **Exit:** owner written approval; M1 startable with no blocking questions.

## M1 – Foundation (COMPLETE)
Backend/frontend scaffolds, config (Pydantic settings), structured logging, Postgres + Alembic, Docker Compose, CI (Ruff, mypy strict, pytest, ESLint, gitleaks), startup safety check for `AD_EXECUTION_ENABLED`. **Exit:** CI green; health endpoint; empty migration with orgs/users/memberships + RLS and a cross-tenant test.

## M2 – Mock Advertising Data (IMPLEMENTED, pending owner review; see docs/MOCK_DATA.md)
Domain models, `AdvertisingDataProvider`, `MockAdvertisingDataProvider` (seeded, realistic distributions, MOCK labels), validation, sync job, contract tests. **Exit:** reproducible dataset; provider swap via config; mock execute raises.

## M3 – PPC Analytics and ML (IMPLEMENTED, steps 1-9 incl. dashboard UI)
Steps: (1) KPI engine, (2) tenant-scoped analytics reader, (3) dashboard and campaign analytics, (4) keyword and
search-term findings, (5) anomaly detection, (6) forecasting + `forecast_runs`, (7) read-only API, (8) docs, (9) dashboard UI.
See [ANALYTICS.md](ANALYTICS.md) and [ML_EVALUATION.md](ML_EVALUATION.md). **Exit status: not fully met.**

- Pre-registered held-out checks that **failed** (recorded as strict known failures, not adjusted): keyword waste
  precision (0.67 vs 0.85), negative-term recall (0.64 vs 0.70), account forecast "no worse than seasonal-naive" (6/10 vs 7/10).
  Anomaly detection passed, exactly at its detection limit (8/10).
- **Verified 2026-11-12:** analytics query timing on the 646k-row seed (dashboard 0.03-0.04 s; keyword findings 0.9-1.2 s; ML_EVALUATION.md section 6). **Not verified:** the `EXPLAIN` index review; the Docker Compose stack.
- **Verified in CI:** backend (full suite incl. Postgres RLS tests; 306 tests at that commit, 840 plus 48 evals now), frontend and gitleaks jobs on `2d1a1bd`. The first run on `2048180` failed only in the gitleaks job (cause unconfirmed; a local gitleaks scan of all history found no leaks).
- **Re-scored on fresh seeds 6-10 with restated truth** (ML_EVALUATION.md 2b): wasted precision and negative recall now pass; negative precision fails (0.77 vs 0.85, recorded as known); negative precision diagnosed as weak converters, not irrelevant terms (not changed). **Forecast re-score on 30 fresh account cases also failed** (63% not worse, mean -6.7% vs seasonal-naive; ML_EVALUATION.md 4b), so the learned forecast is not shown to beat the baseline.
- **Owner decision (2026-11-12):** the default forecast is seasonal-naive; learned models are experimental and opt-in (ADR 0013 update, ML_EVALUATION.md 4c). Negative precision stays a recorded known failure.
- **M3 close-out in progress:** (1) tree cleanup, docs and status files; (2) run the Postgres-backed tests and compose stack; (3) record known limitations; (4) add an independent evaluation signal on fresh seeds.

## M4 – GenAI and RAG (IMPLEMENTED, pending owner review; see docs/ASSISTANT.md)
`LLMClient` port with cost caps, read-only tool calling over analytics, pgvector knowledge base, code-based faithfulness validator, chat API and UI. **Exit:** golden Q&A with traceable numbers: met on the pre-registered counts; run 2 followed a prompt change and is not held-out; a 12-question held-out check passed (all criteria, $0.031). Known gaps listed in ASSISTANT.md.

## M5 – Multi-Agent System (IN PROGRESS; scope in ADR 0018)
LangGraph graph, typed state, specialists, Validator, checkpointing. **Exit:** routing and faithfulness evals pass.

Steps: (1) docs + ADR 0018 **done**; (2-3) graph skeleton, supervisor, three read-only specialists, validator reuse, budgets **done** (`backend/app/application/agents/`, 14 scripted-model tests, no live run yet); (4) reporting **done** (deterministic report + CSV/Markdown/JSON endpoint, plus a `ppc_reporting` specialist; PDF not built; ADR 0020); (5) persisted routing trace **done** (a `route` step in every stored run lists the specialists chosen or marks a fallback); the Postgres LangGraph checkpointer is **deferred to M6**: it cannot be verified here (no Postgres in this environment), read-only runs finish in seconds and are stored immutably so there is nothing to resume, and its real use is the M6 approval interrupt. Run counters and evidence still live on `GraphRun`, not in checkpointed state, and must move when the checkpointer is added; (6) chat API switch (`ASSISTANT_ENGINE`, default single) **done**; (7) pre-registered evaluation **run, partly failed** (held-out and cost criteria pass, routing criterion fails on one unverified answer; see ASSISTANT.md). The R3 failure was later traced to the validator counting numbers echoed from the question (ADR 0019); the fix was re-checked on 3 questions only, not re-scored. **Remaining for M5 exit:** owner review.

## Authentication (between M5 and M6; ADR 0021) – IMPLEMENTED, pending owner review
JWT verification (JWKS, asymmetric only), org selection via `X-Org-Id`, role from membership, role-based route guards, strict startup checks. SQL membership lookup verified on PG 18. **2026-11-13 (ADR 0026):** Auth0 chosen, invitation-only provisioning and frontend sign-in implemented (tests pass); **not yet run against a real Auth0 tenant**; container deployment (nginx CSP, build args) not updated.

## M6 – Optimization and Approval (IMPLEMENTED, database layer UNVERIFIED; ADR 0022)
Approval Center UI added 2026-11-12 (frontend, 12 tests; development-only role menu, no OIDC login yet).
Deterministic bid/budget/negative-keyword proposals with guardrails, lifecycle, hash-bound approval, expiry, idempotency, audit, blocked execution gate, roles. **Not done:** DB verification (no test database), UI, Bid/Budget as graph specialists, Postgres checkpointer, expiry job.

Original scope:
Bid/budget/negative-keyword recommendations, approval workflow, audit logs, gated execution (disabled). **Exit:** guardrail violations = 0; approval flow end-to-end on mock.

## Observability and audit hygiene (ADR 0023) – IMPLEMENTED, pending owner review
Value-level log redaction, access log, safe exception logging, audit for every state-changing route, scrubbed audit content, audit filters. Unverified: SQL audit filters (no test database). Not done: metrics, tracing, alerting, retention.

## M7 – Evaluation and Testing
Unit/integration/agent/ML evals, security and performance tests. **Exit:** coverage and eval targets met.

## M8 – Deployment (IMPLEMENTED as files and docs, UNVERIFIED; ADR 0024)
Hash-pinned lock, hardened images, production Compose with migrate/roles jobs, runtime safety checks, CI image/audit/coverage jobs, DEPLOYMENT.md and OPERATIONS.md. **Verified 2026-11-12:** migrations 0001-0006 up/down on PostgreSQL 18, the startup role check. **Not verified:** any image build, Compose bring-up, restore. **Not done:** OIDC provider and frontend login, TLS, monitoring and log shipping, backup rehearsal, performance measurement.

Original scope:
Cloud deploy, hosted DB, env config, monitoring, docs.

## M9 – Amazon Ads API (PARTLY IMPLEMENTED: endpoint-agnostic layer only; ADR 0025)
Token manager, retry/rate limiting, LWA refresh, safe HTTP client, encrypted token vault, provider skeleton, config checks; all disabled. **Not done and blocked on the owner:** eligibility/access, verified API documentation, header scheme, endpoint mapping, SQL credential store, OAuth consent flow, read-only validation. See docs/AMAZON_ADS_INTEGRATION.md.

Original scope:
Eligibility confirmed, OAuth, `AmazonAdsApiProvider`, reports, sync, approved execution. Only after access is granted.

## M0 task list
| # | Task | Status |
|---|---|---|
| 1 | Resolve owner decisions | Accepted as proposed defaults |
| 2 | Git init, `.gitignore`, `.env.example` | Done |
| 3-8 | PRD, ARCHITECTURE, DATABASE, AGENT, API, SECURITY, IMPLEMENTATION_PLAN, README | Done |
| 9 | ADRs 0001-0010 | Done |
| 10 | Owner review and approval of M0 | Approved 2026-11-02 |

## Environment prerequisites
Python 3.12 present. Node is v26 (non-LTS): pin an LTS in M1. Docker and PostgreSQL not installed.
