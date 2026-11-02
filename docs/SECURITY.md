# Security and Safety

## 1. Mandatory rules
No real campaign changes without explicit human approval. Default `AD_EXECUTION_ENABLED=false`. Mock provider never executes. Never invent Amazon data. Every record keeps source + timestamp. Synthetic data labeled MOCK.

## 2. Secrets
Environment / secret manager via Pydantic `SecretStr`. `.env` gitignored; `.env.example` placeholders only. Secret scanning (gitleaks) in CI. Log redaction filter. Only `VITE_`-prefixed public config reaches the frontend.

## 3. OAuth tokens (M9)
Authorization code flow with `state` and PKCE. Refresh tokens encrypted at application level (AES-GCM, key versioning, key outside the DB). Access tokens short-lived, memory only. Never logged. User-revocable.

## 4. Authentication and authorization
Managed auth provider **[Proposed: Supabase Auth or Auth0]**; JWT verified by FastAPI. RBAC (viewer, analyst, admin, owner), deny by default.

## 5. Tenant isolation
JWT-derived tenant; repository `TenantContext`; Postgres RLS; CI cross-tenant negative tests.

## 6. Rate limiting and resilience
Per-user/tenant API limits; LLM cost caps; outbound Amazon calls via token bucket, backoff, `Retry-After`. Timeouts and circuit breakers on external calls.

## 7. LLM / prompt-injection controls
Agents have no write tools. Retrieved text and tool output treated as data. Structured outputs validated. Validator checks numbers against tool results. No secrets or unnecessary PII sent to the model.

## 8. Audit logging
Append-only; records recommendations, approvals, rejections, execution attempts with actor, before/after, request ID.

## 9. Execution safeguards
- Startup fails if `AD_EXECUTION_ENABLED=true` and any of: non-mock provider, approval service, authorization checks, audit sink is missing.
- Execution requires: approved record by authorized human, unexpired recommendation, unchanged `content_hash`, successful audit write before the call.
- Dry-run first; changes reversible with prior values stored.

## 10. Threat model (STRIDE-lite, to expand in M7)
Spoofing (JWT validation), Tampering (immutable audit, content hash), Repudiation (audit), Information disclosure (RLS, redaction), DoS (rate limits), Elevation (RBAC, no agent write tools).

## 11. Compliance notes
Review Amazon Ads API terms (data storage/use limits) in M9 prep; define privacy policy and retention before onboarding real sellers.
