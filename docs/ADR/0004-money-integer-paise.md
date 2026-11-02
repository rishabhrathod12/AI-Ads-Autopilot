# ADR 0004: Money as integer paise

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Float arithmetic causes financial errors.

## Decision
Store money as BIGINT minor units plus currency code; use Decimal only at calculation edges; derived KPIs computed, not stored.

## Consequences
Exact sums; explicit conversion at API/UI boundaries.
