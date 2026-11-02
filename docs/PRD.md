# Product Requirements Document (PRD)

Status: Approved (M0). Items marked **[Proposed]** are accepted defaults, revisitable.

## 1. Problem
Amazon sellers spend heavily on PPC but struggle to see where money is wasted, which keywords to bid up or down, and how to allocate budget. Existing tools are dashboards; sellers still do the analysis manually.

## 2. Goals
- Accurate PPC analytics (ACOS, ROAS, CTR, CPC, CVR) with decimal-safe money handling.
- Actionable, evidence-backed recommendations: bids, budgets, negative keywords.
- A conversational assistant grounded in verified data, never invented facts.
- Human-in-the-loop approval with a full audit trail.
- Provider-independent design: build on mock data now, Amazon Ads API later.

## 3. Non-goals (MVP)
- Real Amazon Ads API connectivity (M9, subject to access).
- Sponsored Brands / Display **[Proposed]**.
- Automatic, unattended campaign changes (never).
- Billing, subscriptions, deep learning models.

## 4. Personas
Individual seller; professional seller; PPC manager; agency / consultant (multi-account).

## 5. Scope decisions **[Proposed]**
Marketplace Amazon.in, currency INR (stored as paise), Sponsored Products only, mock data first.

## 6. Functional requirements and acceptance criteria
| ID | Module | Requirement | Acceptance criteria |
|---|---|---|---|
| FR-A | Dashboard | Spend, ad sales, ACOS, ROAS, impressions, clicks, orders, CVR, trends | Values match KPI engine; zero denominators handled; date range and MOCK label visible |
| FR-B | Campaign Intelligence | Campaign metrics, comparison, health status, underperformer detection, spend distribution | Health rules documented and tested; comparison across equal-length periods |
| FR-C | Keyword Intelligence | Keyword/search-term analysis, opportunities, high-spend-low-conversion, negative keyword suggestions | Minimum-click thresholds applied; low-data items flagged |
| FR-D | Bid Optimization | Target-ACOS bid proposals with impact estimate and confidence | Change caps enforced; min-data rules; evidence attached |
| FR-E | Budget Optimization | Utilization, constraint detection, reallocation, scenario simulation | Total budget conserved; scenarios labeled estimates |
| FR-F | Predictive Intelligence | Forecast spend, sales, ACOS with intervals | Beats naive baseline or states it does not; MAE/RMSE/MAPE reported; refuses on short history |
| FR-G | AI Assistant | Answers questions using retrieved data and tool results | Every number traceable to a tool result; "insufficient data" allowed |
| FR-H | Approval Center | View, approve, reject, track recommendations | Immutable decision record; audit entry per decision |
| FR-I | Reports | Downloadable PPC report **[Proposed: PDF + CSV]** | Report numbers equal dashboard numbers |
| FR-J | Multi-tenancy | Organizations, memberships, roles | Cross-tenant access impossible (tested) |

## 7. Non-functional requirements
- **Correctness:** unit-tested metrics; money as integer paise.
- **Security:** see SECURITY.md; secrets never in code, logs or bundles.
- **Provenance:** every record has source, timestamp, report date range.
- **Reliability:** graceful handling of missing data and provider/LLM errors.
- **Observability:** structured logs, request IDs, agent traces.
- **Performance targets (initial):** dashboard < 1s on mock volumes; assistant first token < 3s.
- **Quality:** Ruff, strict mypy, ESLint, CI on every PR.

## 8. Success metrics
Metric correctness (100% of golden tests), recommendation validity (0 guardrail violations), assistant faithfulness (all numbers traceable), forecast vs baseline improvement, zero cross-tenant leaks in tests.

## 9. Assumptions
Seller Central account is active (owner-reported); no Ads API access exists; mock data approximates real distributions but proves method, not real-world accuracy.

## 10. Open questions
See IMPLEMENTATION_PLAN.md "Owner decisions".
