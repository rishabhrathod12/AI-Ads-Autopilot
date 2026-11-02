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

## M3 – PPC Analytics and ML (IMPLEMENTED, steps 1-8; step 9 optional UI not started)
Steps: (1) KPI engine, (2) tenant-scoped analytics reader, (3) dashboard and campaign analytics, (4) keyword and
search-term findings, (5) anomaly detection, (6) forecasting + `forecast_runs`, (7) read-only API, (8) docs.
See [ANALYTICS.md](ANALYTICS.md) and [ML_EVALUATION.md](ML_EVALUATION.md). **Exit status: not fully met.**

- Pre-registered held-out checks that **failed** (recorded as strict known failures, not adjusted): keyword waste
  precision (0.67 vs 0.85), negative-term recall (0.64 vs 0.70), account forecast "no worse than seasonal-naive" (6/10 vs 7/10).
  Anomaly detection passed, exactly at its detection limit (8/10).
- **Not verified:** analytics query timing (< 1 s on the 646k-row seed) and the `EXPLAIN` index review; the Docker Compose stack.
- **Verified in CI:** backend (full suite incl. Postgres RLS tests; 306 tests pass locally), frontend and gitleaks jobs on `2d1a1bd`. The first run on `2048180` failed only in the gitleaks job (cause unconfirmed; a local gitleaks scan of all history found no leaks).
- Owner decisions pending: how to treat the three failed checks (re-state criteria and validate on fresh seeds, change
  rules, or accept); whether to build the optional minimal UI (step 9) before M4.

## M4 – GenAI and RAG
`LLMClient`, tool calling over analytics, pgvector knowledge base with source attribution, single assistant. **Exit:** golden Q&A with traceable numbers.

## M5 – Multi-Agent System
LangGraph graph, typed state, specialists, Validator, checkpointing. **Exit:** routing and faithfulness evals pass.

## M6 – Optimization and Approval
Bid/budget/negative-keyword recommendations, approval workflow, audit logs, gated execution (disabled). **Exit:** guardrail violations = 0; approval flow end-to-end on mock.

## M7 – Evaluation and Testing
Unit/integration/agent/ML evals, security and performance tests. **Exit:** coverage and eval targets met.

## M8 – Deployment
Cloud deploy, hosted DB, env config, monitoring, docs.

## M9 – Amazon Ads API
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
