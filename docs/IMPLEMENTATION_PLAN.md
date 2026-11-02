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

## M3 – PPC Analytics and ML
KPI engine (golden tests), campaign/keyword analytics, forecasting with baselines, anomaly detection, evaluation (MAE/RMSE/MAPE), leakage-safe splits. **Exit:** metrics verified; models compared to naive baselines honestly.

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
