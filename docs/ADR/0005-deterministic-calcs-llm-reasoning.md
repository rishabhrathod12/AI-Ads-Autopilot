# ADR 0005: Deterministic calculations, LLM for reasoning

Status: Accepted (owner approved M0, 2026-11-02)

## Context
LLMs hallucinate numbers.

## Decision
All KPIs, bid math, budget math and forecasts run in tested Python tools; LLMs plan, select tools and explain; a non-LLM validator checks numbers against tool results.

## Consequences
Trustworthy outputs; slightly more tool plumbing.
