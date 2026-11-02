# Database Design

PostgreSQL 16 + pgvector. SQLAlchemy 2.x, Alembic migrations.

## 1. Conventions
- UUID primary keys; `created_at`, `updated_at` (UTC `timestamptz`).
- Money: `BIGINT` paise + currency code. Never floats.
- Tenant tables carry `organization_id` (NOT NULL, indexed).
- Data tables carry `data_source` (`MOCK` | `AMAZON_ADS_API`), `ingested_at`, `external_id`.
- Derived KPIs (ACOS, ROAS, CTR, CPC, CVR) are computed, not stored as truth.
- Dates for reports are in the profile's timezone; attribution window recorded.

## 2. Entities
| Table | Key columns | Relationships |
|---|---|---|
| organizations | name, plan | tenant root |
| users | email, auth_subject | |
| memberships | user_id, organization_id, role | user <-> org (M:N) |
| seller_accounts | organization_id, marketplace, region, status, provider_type | org 1:N |
| provider_credentials | seller_account_id, encrypted_refresh_token, key_version, scopes | empty in Phase A |
| advertising_profiles | seller_account_id, profile_id, country, currency, timezone | |
| campaigns | profile_id, name, state, daily_budget_paise, targeting_type, bidding_strategy | |
| ad_groups | campaign_id, name, default_bid_paise, state | |
| keywords_targets | ad_group_id, text, match_type, bid_paise, state | keywords and ASIN/category targets |
| search_terms | ad_group_id, keyword_target_id?, term | |
| report_runs | profile_id, report_type, date_start, date_end, source, status | provenance anchor |
| performance_daily | entity_type, entity_id, date, impressions, clicks, spend_paise, sales_paise, orders, units, attribution_window, report_run_id | monthly partitions later |
| agent_runs / agent_steps | thread_id, user_id, plan, tool calls, outputs, tokens | trace |
| knowledge_documents / knowledge_chunks | organization_id? (null = global), source, embedding | RAG |
| recommendations | type, target entity, current/proposed value, rationale, evidence_refs, confidence, expected_impact, status, agent_run_id, expires_at, content_hash | |
| approval_records | recommendation_id, decided_by, decision, comment, decided_at | immutable |
| execution_attempts | recommendation_id, approval_id, status, provider_response, dry_run | |
| audit_logs | organization_id, actor, action, entity, before, after, request_id | append-only |

`performance_daily` is a single fact table keyed by entity type so campaign, ad group, keyword and search-term metrics share one shape.

## 3. Tenant isolation (three layers)
1. API derives `organization_id` from verified JWT + membership; never from request bodies.
2. Repositories require `TenantContext`.
3. Postgres RLS: `USING (organization_id = current_setting('app.org_id')::uuid)`; the app DB role is not a superuser and cannot bypass RLS. Child tables without a direct `organization_id` get one denormalized so RLS is simple and indexable.

Global knowledge documents (`organization_id IS NULL`) are readable by all, writable by none via the app.

## 4. Audit and immutability
`audit_logs` and `approval_records`: INSERT only (UPDATE/DELETE revoked). Recommendation status changes are audited.

## 5. Indexing
`(organization_id, entity_type, entity_id, date)` on `performance_daily`; GIN/HNSW on embeddings; unique `(profile_id, external_id)` on synced entities.

## 6. Migrations
Alembic only; no manual DDL; every migration reviewed; RLS policies are part of migrations and covered by cross-tenant tests.

## 7. Implemented so far
- M1: `organizations`, `users`, `memberships`, `audit_logs` (insert-only), RLS (`0001`).
- M2: `seller_accounts`, `advertising_profiles`, `campaigns`, `ad_groups`, `keywords_targets`, `search_terms`,
  `report_runs`, `performance_daily` (`0002`). All have RLS; the app role has no DELETE. Internal ids are deterministic
  `uuid5(organization_id, kind:external_id)`, which makes syncs idempotent without lookups. `performance_daily` uses a
  composite primary key `(organization_id, entity_type, entity_id, date)` and CHECK constraints for metric invariants.
