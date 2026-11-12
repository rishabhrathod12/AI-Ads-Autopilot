# Security and Safety

## 1. Mandatory rules
No real campaign changes without explicit human approval. Default `AD_EXECUTION_ENABLED=false`. Mock provider never executes. Never invent Amazon data. Every record keeps source + timestamp. Synthetic data labeled MOCK.

## 2. Secrets
Environment / secret manager via Pydantic `SecretStr`. `.env` gitignored; `.env.example` placeholders only. Secret scanning (gitleaks) in CI. Log redaction filter. Only `VITE_`-prefixed public config reaches the frontend.

## 3. OAuth tokens (M9)
**Implemented as a disabled, endpoint-agnostic layer (ADR 0025):** AES-256-GCM encryption of refresh tokens with versioned keys and owner binding, in-memory access tokens, no tokens in logs, https-only no-redirect client with a path allowlist. The OAuth consent flow (state, PKCE) and the SQL credential store are not built. Target design:

Authorization code flow with `state` and PKCE. Refresh tokens encrypted at application level (AES-GCM, key versioning, key outside the DB). Access tokens short-lived, memory only. Never logged. User-revocable.

## 4. Authentication and authorization
Managed OIDC provider issues JWTs; FastAPI verifies them (asymmetric algorithms only, issuer/audience/expiry/subject required; ADR 0021). The caller selects an organization with `X-Org-Id`; the role comes from the membership row, never from the token or a header. RBAC (viewer, analyst, admin, owner), deny by default. Implemented and unit-tested; the SQL membership lookup is **unverified** until the database tests run. Open: provider choice, user provisioning, failed-login rate limiting, revocation, and `users` is not RLS-protected.

## 5. Tenant isolation
JWT-derived tenant; repository `TenantContext`; Postgres RLS; CI cross-tenant negative tests.

## 6. Rate limiting and resilience
Per-user/tenant API limits; LLM cost caps; outbound Amazon calls via token bucket, backoff, `Retry-After`. Timeouts and circuit breakers on external calls.

## 7. LLM / prompt-injection controls
Agents have no write tools. Retrieved text and tool output treated as data. Structured outputs validated. Validator checks numbers against tool results. No secrets or unnecessary PII sent to the model.

## 8. Audit logging
**Implemented (ADR 0023):** every state-changing route writes an audit entry with actor, organization, request id and before/after, in the same transaction; content is scrubbed of credentials and emails first; access logs omit query strings, headers and bodies; log values are redacted, not just secret-named fields. SQL audit queries verified on PostgreSQL 18.6 (2026-11-12). Target design below.

Append-only; records recommendations, approvals, rejections, execution attempts with actor, before/after, request ID.

## 8b. Production hardening (ADR 0024, unverified in containers)
Production refuses to start without JWT auth, serves no API docs, and refuses to serve as a database role that can bypass RLS; the backend runs as the limited `adpilot_app` login role, non-root, read-only filesystem, no capabilities; nginx sends CSP and other security headers; dependencies are hash-pinned and scanned (no known vulnerabilities on 2026-11-12). Open: TLS/HSTS in front, secret manager and rotation, log shipping and alerting, digest-pinned base images.

## 9. Execution safeguards
**Implemented (ADR 0022, database layer verified on PostgreSQL 18.6 on 2026-11-12):** approval is bound to the content hash and expires; only admin/owner decide; every decision, expiry and blocked execution is audited in the same transaction; the execute endpoint always answers 409 and no code calls a provider. The list below is the target for M9.

- Startup fails if `AD_EXECUTION_ENABLED=true` and any of: non-mock provider, approval service, authorization checks, audit sink is missing.
- Execution requires: approved record by authorized human, unexpired recommendation, unchanged `content_hash`, successful audit write before the call.
- Dry-run first; changes reversible with prior values stored.

## 10. Threat model (STRIDE-lite, to expand in M7)
Spoofing (JWT validation), Tampering (immutable audit, content hash), Repudiation (audit), Information disclosure (RLS, redaction), DoS (rate limits), Elevation (RBAC, no agent write tools).

## 11. Compliance notes
Review Amazon Ads API terms (data storage/use limits) in M9 prep; define privacy policy and retention before onboarding real sellers.
