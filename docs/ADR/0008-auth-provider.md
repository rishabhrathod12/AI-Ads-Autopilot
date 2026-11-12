# ADR 0008: Authentication provider

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Hand-rolled auth is high risk.

## Decision
Managed provider issuing JWTs verified by FastAPI. The verification design is in ADR 0021 and is provider-independent; the specific provider (Supabase Auth, Auth0, Keycloak or other OIDC) is **still an open owner decision**, required before deployment.

## Consequences
Less security surface; vendor dependency.
