# System Architecture

## 1. Style
Modular monolith, layered, ports-and-adapters. One deployable backend, one SPA frontend, one PostgreSQL (with pgvector).

## 2. Container view
```
React SPA --HTTPS/JSON (+SSE)--> FastAPI
                                   |
         API layer: routers, auth, rate limit, DTOs
                                   |
         Application layer: use-case services
           Analytics | Recommendation | Approval | Forecast | Knowledge | Agent runtime
                                   |
         Domain layer: entities, Money, Metrics, rules (pure Python, no I/O)
                                   |
         Ports: AdvertisingDataProvider, LLMClient, VectorStore, Repositories, ExecutionGateway
                                   |
         Infrastructure: MockProvider | AmazonAdsApiProvider (M9) | OpenAI | SQLAlchemy/pgvector
```

## 3. Layer rules
- Domain imports nothing from other layers.
- Application imports domain and ports only.
- Infrastructure implements ports; API wires everything via dependency injection.
- Agents and services never import a concrete provider.
- Routers never call agents/providers directly; they call application services.

## 4. Provider abstraction
`AdvertisingDataProvider` (read: profiles, campaigns, ad groups, targets, reports; write: `execute_change`, gated). Selected by `ADS_PROVIDER=mock|amazon`. `MockAdvertisingDataProvider.execute_change` always raises. Interface shaped after the real Ads API (async report generation, report lag, attribution windows) so Amazon adapter does not force redesign. Contract tests run against every provider.

## 5. Data flow
1. Sync job pulls from provider -> normalizes to domain models -> stores with `data_source`, `ingested_at`, `report_run_id`.
2. Analytics/ML/agents read from DB only (reproducible, rate-limit safe).
3. Recommendations stored with evidence references -> approval -> (gated) execution -> audit.

## 6. Background work
In-process worker / Postgres-backed queue for syncs and long agent runs. No Redis/Celery until measured need.

## 7. Multi-tenancy
Shared schema, `organization_id` on tenant tables, Postgres RLS, tenant derived from verified JWT (see DATABASE_DESIGN.md, SECURITY.md).

## 8. LLM access
Behind `LLMClient` port (OpenAI first). Structured outputs; step/token budgets; response caching where safe.

## 9. Observability
`structlog` JSON logs with request ID and tenant ID, OpenTelemetry-ready, agent traces in `agent_runs`/`agent_steps`, optional Langfuse/LangSmith later.

## 10. Deployment (decided in M8)
Docker images; frontend on Vercel; backend on Railway/Render; managed Postgres. Local dev via Docker Compose (requires Docker Desktop, currently not installed).

## 11. Backend layout (planned, M1)
```
backend/app/{domain,application,infrastructure,api}/
backend/tests/{unit,integration,contract,evals}/
```
