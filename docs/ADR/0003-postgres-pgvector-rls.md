# ADR 0003: PostgreSQL, pgvector and RLS multi-tenancy

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Need relational data, vectors, checkpoints, and tenant isolation.

## Decision
One PostgreSQL with pgvector; shared schema with organization_id and Row-Level Security; non-superuser app role.

## Consequences
Fewer moving parts; isolation enforced even if code forgets a filter. RLS needs careful testing and connection-level tenant setting.
