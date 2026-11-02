# ADR 0007: Execution disabled by default and fail-safe startup

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Real ad changes spend real money.

## Decision
AD_EXECUTION_ENABLED=false default; app refuses to boot if true without non-mock provider, approval service, authorization checks and audit sink; execution requires approval record, unchanged content hash, prior audit write.

## Consequences
Safe by default; enabling execution is deliberate and tested.
