# ADR 0014: Development-only tenant header; production refuses to start

Status: Accepted (interim, until real authentication lands)

## Context
M3's API needs a tenant on every request, but authentication (ADR 0008) is not built. Reading the tenant from a
request header is convenient for development and dangerous anywhere real data exists: any caller could read any
seller's data.

## Decision
- The tenant comes from an `X-Dev-Org-Id` header, resolved into a `TenantContext`. Everything downstream is isolated
  by PostgreSQL row-level security regardless of how the tenant was resolved.
- `create_app` raises at startup when `APP_ENV=production`, and the resolver independently refuses requests there.
- A missing or invalid header is 401; no database is 503; a tenant with no data gets 404, never another tenant's numbers.
- Real authentication is required before any non-local deployment and before the approval workflow (M6), which needs
  a trustworthy actor identity.

## Consequences
Safe to demo locally; impossible to deploy by accident. The interface (`resolve_tenant`) is the single place to swap
in JWT verification. Tests cover the missing header, the production refusal and end-to-end tenant isolation over HTTP.
