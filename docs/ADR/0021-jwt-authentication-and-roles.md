# ADR 0021: Provider-independent JWT authentication, org selection and roles

Status: Accepted (implemented 2026-11-12; supersedes the "proposed" part of ADR 0008; the identity provider is still to be chosen)

## Context
The development tenant header (ADR 0014) lets any caller read any tenant's data, so nothing could be deployed, and the
approval workflow needs a verified person with a role. Hand-rolled password handling is high risk (ADR 0008).

## Decision
- **Verify, do not issue.** A managed identity provider issues JWTs; the API only verifies them against the provider's
  published keys (JWKS). Supabase Auth, Auth0, Keycloak or any OIDC provider fit: only configuration changes
  (`AUTH_JWT_ISSUER`, `AUTH_JWT_AUDIENCE`, `AUTH_JWKS_URL`, optional `AUTH_JWT_ALGORITHMS`).
- **Strict verification.** Asymmetric algorithms only (default RS256, ES256), so `alg: none` and HMAC-with-public-key
  tokens are rejected, at construction time and per token. `exp`, `sub`, `iss`, `aud` are required; leeway 30 s; token
  length capped. Failures return a generic 401 with `WWW-Authenticate: Bearer` and no reason; tokens are never logged.
- **The tenant is selected, never asserted.** `X-Org-Id` picks which of the caller's organizations to act in; the role
  comes from the `memberships` row, looked up inside that organization's own RLS scope. Not a member: 403. A role claim in
  the token is ignored. Missing or malformed `X-Org-Id`: 400.
- **Roles** viewer < analyst < admin < owner, deny by default. Reads need viewer; creating forecasts and using the
  assistant need analyst; approval (M6) will need admin.
- **Configuration decides the mode, requests never do.** With all three auth settings present, bearer tokens are required
  and the development headers are ignored. With none, development mode works as before (never in production). A
  *partial* configuration is refused at startup in every environment, because it would silently fall back to the
  development header. Production starts only with full configuration and an https JWKS URL.
- Authorization runs before a tenant session opens.

## Consequences
- Any OIDC provider works with no code change; the provider choice is deferred but required before deployment.
- A user must already exist in `users` (matched by `auth_subject`) with a membership. **There is no sign-up or invitation
  flow**; provisioning is a later task.
- `users` has no RLS (ADR 0003): any database session as the app role can read every user's email. Tracked as a finding.
- Database-level behavior (membership lookup under RLS) is covered by `tests/integration/test_membership.py`, which could not
  be run in the development environment: **unverified** until `TEST_DATABASE_URL` is available.
- The frontend still sends the development header; it needs an OIDC login flow before it can talk to an authenticated API.
- No rate limiting on failed authentication yet; no token revocation beyond expiry; JWKS fetch failure fails closed (401).
