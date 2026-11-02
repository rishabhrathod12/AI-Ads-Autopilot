# ADR 0010: LLM provider abstraction

Status: Accepted (owner approved M0, 2026-11-02)

## Context
Avoid vendor lock-in and enable testing with fakes.

## Decision
LLMClient port; OpenAI adapter first; fake client for tests; structured outputs required.

## Consequences
Swappable and testable; small abstraction cost.
