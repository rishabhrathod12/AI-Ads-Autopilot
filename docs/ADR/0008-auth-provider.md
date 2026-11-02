# ADR 0008: Authentication provider

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Hand-rolled auth is high risk.

## Decision
Proposed: managed provider (Supabase Auth or Auth0) issuing JWTs verified by FastAPI. Final choice awaits owner decision.

## Consequences
Less security surface; vendor dependency.
