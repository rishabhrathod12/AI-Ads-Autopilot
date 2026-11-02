# ADR 0009: Background job approach

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Syncs and long agent runs need async execution; Redis/Celery add complexity.

## Decision
Start with in-process worker / Postgres-backed queue; revisit with measured need.

## Consequences
Minimal infrastructure; may need migration under load.
